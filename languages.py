# languages.py
"""
Fallback translations for the app (limited set).

Supported languages:
- Portuguese (Brazil)  -> pt, pt-BR
- English (default/original msgid) -> en, en-*
- Spanish              -> es, es-*

Exported API:
- t(msgid: str, locale_str: str | None) -> str
- is_rtl(locale_str: str | None) -> bool

Behavior:
- Uses built-in PT/ES dictionaries.
- English is the original msgid (no dictionary needed).
- If a key is missing in the chosen language, returns the original msgid (English).
- Optional external override packs: put JSON files at data/fallback_locales/<lang>.json
  (only pt.json and es.json are loaded). These override built-ins for that language.
"""
from __future__ import annotations
from pathlib import Path
import json
import os
import locale
import re

# --------------- Locale utilities ---------------
_SUPPORTED_BASES = {"en", "pt", "es"}
_ALIAS: dict[str, str] = {}


def _norm_locale(tag: str | None) -> str:
    if not tag:
        return "en"
    t = tag.strip().replace("_", "-")
    if not t:
        return "en"
    parts = t.split("-", 1)
    base = parts[0].lower()

    # Only keep en/pt/es. Anything else falls back to English.
    if base not in _SUPPORTED_BASES:
        return "en"

    if len(parts) == 2 and parts[1]:
        region = parts[1].upper()
        t = f"{base}-{region}"
    else:
        t = base

    t_low = t.lower()
    if t_low in _ALIAS:
        return _ALIAS[t_low]
    return t


def _lang_key(tag: str | None) -> str:
    # For dictionary selection we only need the base code (pt, es, en)
    t = _norm_locale(tag)
    return t.split("-", 1)[0]


def is_rtl(locale_str: str | None) -> bool:
    # With only en/pt/es supported, RTL is always False.
    return False


# --------------- Locale tag canonicalization (shared by server.py and viewer.py) ---------------
# Both used to keep their own near-identical copy of this; kept here as the single
# source of truth so OS-locale detection (viewer.py, before Flask is up) and
# request/settings locale resolution (server.py) never drift apart.
_LANG_WORD_TO_CODE = {
    "english": "en",
    "portuguese": "pt", "portugues": "pt", "português": "pt",
    "spanish": "es", "espanol": "es", "español": "es",
    "french": "fr",
    "german": "de",
    "italian": "it",
}
_REGION_WORD_TO_CODE = {
    "br": "BR", "brazil": "BR", "brasil": "BR", "brazilian": "BR",
    "us": "US", "usa": "US", "states": "US", "united": "US", "america": "US",
    "uk": "GB", "gb": "GB",
    "pt": "PT", "portugal": "PT",
    "es": "ES", "spain": "ES", "espana": "ES", "españa": "ES",
    "mx": "MX", "mexico": "MX",
}


def canon_locale(tag: str | None) -> str:
    """Canonicaliza uma tag de idioma para 'xx' ou 'xx_YY' (ex.: 'en', 'pt_BR').

    Aceita tanto códigos curtos ('en-US', 'pt_BR') quanto nomes por extenso que o
    Windows às vezes devolve ('English_United States'). Mantém 'auto' como está
    (sentinela de "seguir preferência do sistema/instalador").
    """
    if not tag:
        return "en"
    s = str(tag).strip()
    if not s:
        return "en"
    if s.lower() == "auto":
        return "auto"

    # Descarta encoding/modificadores (en_US.UTF-8, pt_BR@latin, en-US:en)
    s = s.split(":", 1)[0].split(".", 1)[0].split("@", 1)[0]
    s = s.replace("-", "_").replace(" ", "_")

    toks = [re.sub(r"[^A-Za-z]", "", x) for x in s.split("_") if x]
    toks = [t for t in toks if t]
    if not toks:
        return "en"

    first_low = toks[0].lower()
    rest_low = [t.lower() for t in toks[1:]]

    word_lang = _LANG_WORD_TO_CODE.get(first_low)
    if word_lang == "pt":
        # Nome por extenso ("Portuguese"/"Português"): só assume BR quando explícito;
        # sem sinal de Brasil, fica no "pt" genérico (não força nenhuma região).
        if any(w in ("br", "brazil", "brasil", "brazilian") for w in rest_low):
            return "pt_BR"
        return "pt"

    lang = word_lang or (first_low[:2] if len(first_low) >= 2 else "en")

    region = ""
    for tok, low in zip(toks[1:], rest_low):
        if len(tok) == 2 and tok.isalpha():
            region = tok.upper()
            break
        if low in _REGION_WORD_TO_CODE:
            region = _REGION_WORD_TO_CODE[low]
            break

    return f"{lang}_{region}" if region else lang


def os_locale_tag() -> str:
    """Idioma do SISTEMA OPERACIONAL, já canonicalizado ('en', 'pt_BR', ...).

    Fonte única para viewer.py (título da janela + diálogos nativos) e
    server.py (locale de request quando não há preferência salva) — antes
    cada um tinha sua própria detecção e podiam divergir. Nunca levanta;
    cai em 'en'.
    """
    # Windows: a API nativa é a mais confiável — o usuário pode ter trocado o
    # idioma de exibição sem mexer nas variáveis de ambiente.
    if os.name == "nt":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85):
                return canon_locale(buf.value)
        except Exception:
            pass
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\International") as k:
                loc = winreg.QueryValueEx(k, "LocaleName")[0]
                if loc:
                    return canon_locale(loc)
        except Exception:
            pass

    for getter in (lambda: locale.getlocale()[0], lambda: locale.getdefaultlocale()[0]):
        try:
            val = getter()
            if val:
                return canon_locale(val)
        except Exception:
            pass

    for k in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(k)
        if v:
            return canon_locale(v)

    return "en"


# --------------- External overrides ---------------
_BASE_DIR = Path(__file__).resolve().parent
_EXT_DIR = _BASE_DIR / "data" / "fallback_locales"
_EXTERNAL_CACHE: dict[str, dict[str, str]] = {}


def _load_external(lang_key: str) -> dict[str, str]:
    # Only load pt/es external packs (keep the app limited to en/pt/es).
    if lang_key not in ("pt", "es"):
        return {}
    if lang_key in _EXTERNAL_CACHE:
        return _EXTERNAL_CACHE[lang_key]

    path = _EXT_DIR / f"{lang_key}.json"
    data: dict[str, str] = {}
    try:
        if path.exists():
            raw = json.loads(path.read_text("utf-8"))
            if isinstance(raw, dict):
                data = {str(k): str(v) for k, v in raw.items()}
    except Exception:
        data = {}

    _EXTERNAL_CACHE[lang_key] = data
    return data


# --------------- Built-in fallback dictionaries ---------------
# PT-BR/PT-PT fallback (full + extras)
_PT: dict[str, str] = {
    "Simple Sales Management": "Gestor Simples de Vendas",
    "Simple Sales Management (SSM)": "Gestor Simples de Vendas (SSM)",
    "SSM": "SSM",
    "Clients & Sales": "Clientes & Vendas",
    "Navigation": "Navegação",
    "Business": "Negócio",
    "New Sale": "Nova Venda",
    "Sales": "Vendas",
    "Clients": "Clientes",
    "Business (Store Data)": "Negócio (Dados da Loja)",
    "Add": "Adicionar",
    "Remove image": "Remover imagem",
    "Logo": "Logo",
    "Store Name": "Nome da Loja",
    "Phone": "Telefone",
    "Tax ID": "ID Fiscal",
    "Save": "Salvar",
    "Save File": "Salvar arquivo",
    "Select Directory": "Escolher diretório",
    "Open File": "Abrir arquivo",
    "Images": "Imagens",
    "All files": "Todos os arquivos",
    # Business page (genéricos, sem nicho)
    "ACME Co.": "ACME Co.",
    "(00) 0000-0000": "(00) 0000-0000",
    "00000-000": "00000-000",
    "123 Main St": "Rua Principal, 123",
    "City/State": "Cidade/Estado",
    "Client": "Cliente",
    "Code": "Código",
    "6 digits (e.g., 012345)": "6 dígitos (ex.: 012345)",
    "Automatic": "Automático",
    "Show requirements": "Mostrar requisitos",
    "Generate code automatically": "Gerar código automaticamente",
    "Requirements: exactly": "Requisitos: exatamente",
    "numeric digits (e.g., 012345).": "dígitos numéricos (ex.: 012345).",
    "Name": "Nome",
    "Address": "Endereço",
    "Contact": "Contato",
    "Description": "Descrição",
    "Client name": "Nome do cliente",
    "Street & number": "Rua e número",
    "Phone or email": "Telefone ou e-mail",
    "Client notes": "Observações do cliente",
    "Sale": "Venda",
    "Product": "Produto",
    # ✅ management.html, modo "sale" (Nova Venda)
    "Product or service": "Produto ou serviço",
    "Only numbers": "Apenas números",
    "Only numbers lower than the initial value.": "Apenas números menores que o valor inicial.",
    # ==== Generic currency labels (no symbols) ====
    "Amount": "Valor",
    "Details": "Detalhes",
    "Sell": "Vender",
    "No Discount": "Sem Desconto",
    "Code must have exactly 6 digits.": "O código deve ter exatamente 6 dígitos.",
    "Invalid code.": "Código inválido.",
    "Filters": "Filtros",
    "Order": "Ordem",
    "Ascending": "Crescente",
    "Descending": "Decrescente",
    "Month": "Mês",
    "All": "Todos",
    "Year": "Ano",
    "List": "Lista",
    "Actions": "Ações",
    "Search": "Pesquisar",
    "Select": "Selecionar",
    "Back to top": "Voltar ao topo",
    "No data": "Sem dados",
    "No sales yet": "Sem vendas ainda",
    "No clients yet": "Sem clientes ainda",
    "Delete client": "Excluir cliente",
    "Confirm deletion": "Confirmar exclusão",
    "Close": "Fechar",
    "Delete": "Excluir",
    "Cancel": "Cancelar",
    "(Blank)": "(Vago)",
    "All sales from this client will be removed.": "Todas as vendas deste cliente serão removidas.",
    "Failed to delete the client.": "Falha ao excluir o cliente.",
    "Error deleting the client.": "Erro ao excluir o cliente.",
    "Subtotals": "Subtotais",
    "Gross": "Bruto",
    "Final": "Final",
    "Date/Time": "Data/Hora",
    # ✅ ADIÇÃO (msgid usado pelo código)
    "Date/Hour": "Data/Hora",
    "Print receipt": "Imprimir recibo",
    "Delete sale": "Excluir venda",
    "This action is irreversible.": "Esta ação é irreversível.",
    "Failed to delete the sale.": "Falha ao excluir a venda.",
    "Error deleting.": "Erro ao excluir.",
    "Receipt": "Recibo",
    "Receipt image": "Imagem do recibo",
    "Generate PDF": "Gerar PDF",
    "Could not generate the PDF.": "Não foi possível gerar o PDF.",
    "Failed to save the PDF.": "Falha ao salvar o PDF.",
    "Failed to generate the PDF.": "Falha ao gerar o PDF.",
    "STORE NAME (e.g., Example Optics)": "NOME DA LOJA (ex.: Loja Exemplo)",
    "PHONE: (00) 0000-0000": "TELEFONE: (00) 0000-0000",
    "ADDRESS (e.g., Example St, 123)": "ENDEREÇO (ex.: Rua Exemplo, 123)",
    "TAX ID: 00.000.000/0000-00": "ID FISCAL: 00.000.000/0000-00",
    "CLIENT": "CLIENTE",
    "CODE": "CÓDIGO",
    "NAME": "NOME",
    "SALE": "VENDA",
    "DESCRIPTION": "DESCRIÇÃO",
    "PRICE": "PREÇO",
    "ZIP": "CEP",
    "TAX ID": "ID FISCAL",
    "Date:": "Data:",
    "Business data saved.": "Dados da loja salvos.",
    "Fill the product.": "Preencha o produto.",
    "Name is required.": "O nome é obrigatório.",
    "Enter a valid numeric amount (e.g., 1,234.56 or 1.234,56).": "Informe um valor numérico válido (ex.: 1.234,56 ou 1,234.56).",
    "Enter a valid final amount (e.g., 1,000.00) or keep 'No Discount'.": "Informe um valor final válido (ex.: 1.000,00) ou mantenha 'Sem Desconto'.",
    "Final amount cannot be greater than the original amount.": "O valor final não pode ser maior que o valor original.",
    "With 'Automatic' checked, fill client and sale details.": "Com 'Automático' marcado, preencha os dados do cliente e da venda.",
    "Sale created successfully!": "Venda criada com sucesso!",
    "Code must have exactly 6 digits (e.g., 012345).": "O código deve ter exatamente 6 dígitos (ex.: 012345).",
    # Settings UI
    "Settings": "Configurações",
    "Appearance": "Aparência",
    "Directories": "Diretórios",
    "Language": "Idioma",
    "Theme": "Tema",
    "Data directory": "Diretório de dados",
    "Default PDF directory": "Diretório padrão de PDF",
    "Explorer directory": "Diretório do Explorer",
    "Open": "Abrir",
    "Set to Downloads": "Usar Downloads",
    "Choose…": "Escolher…",
    "Settings saved.": "Configurações salvas.",
    # ✅ settings.html (novas keys)
    "Data": "Dados",
    "Export": "Exportar",
    "Import": "Importar",
    "Reset": "Redefinir",
    "Reset all data": "Redefinir todos os dados",
    "Deletes all sales, clients and business data and restarts the initial setup. This cannot be undone.": "Exclui todos os dados de vendas, clientes e negócio e reinicia a configuração inicial. Não é possível desfazer.",
    "Enter a valid directory.": "Informe um diretório válido.",
    "Could not open in Explorer.": "Não foi possível abrir no Explorer.",
    "Explorer integration is unavailable outside the desktop app.": "A integração com o Explorer não está disponível fora do aplicativo desktop.",
    "Error opening the directory.": "Erro ao abrir o diretório.",
    "Downloading…": "Baixando…",
    # Extras para o menu ⋮ e botões
    "Export spreadsheet": "Exportar planilha",
    "Print receipts": "Imprimir recibos",
    "Selected sales": "Vendas selecionadas",
    "Nothing selected": "Nada selecionado",
    "Spreadsheet": "Planilha",
    "Delete sales": "Excluir vendas",
    "Confirm": "Confirmar",
    "Edit": "Editar",
    "Failed to save.": "Falha ao salvar.",
    "Failed to delete.": "Falha ao excluir.",
    # Nomes base de arquivos (CSV)
    "clients_spreadsheet.csv": "planilha_clientes.csv",
    "sales_spreadsheet.csv": "planilha_vendas.csv",
    "Failed to generate spreadsheet.": "Falha ao gerar a planilha.",
    # Feedback / compat
    "Saved": "Salvo",
    "Saving…": "Salvando…",
    "Failed to save": "Falha ao salvar",
    "Open in Explorer": "Abrir no Explorer",
    # ===== Added for management.html (search bar + clients list) =====
    "Select all": "Selecionar todos",
    "Selected count": "Quantidade selecionada",
    "Print": "Imprimir",
    "Select items to export.": "Selecione itens para exportar.",
    "Select items to delete.": "Selecione itens para excluir.",
    "Select an item in the list to edit.": "Selecione um item na lista para editar.",
    "Select a sale to print.": "Selecione uma venda para imprimir.",
    "Press ESC or Cancel to exit.": "Pressione ESC ou Cancelar para sair.",
    # ===== management.html, modo "sale" (Client popup + discount modal) =====
    "Register client": "Cadastrar cliente",
    "New client": "Novo cliente",
    "Tap to add": "Toque para adicionar",
    "Change": "Alterar",
    "Remove": "Remover",
    "Apply": "Aplicar",
    "No clients": "Sem clientes",
    # ===== Added for welcome.html (first setup) =====
    "Welcome": "Bem-vindo",
    "Set up your management to get started.": "Configure seu gestor para começar.",
    "Setup steps": "Etapas de configuração",
    "Introduction": "Introdução",
    "Keeps clients and sales in one place, with quick search and receipts that already carry your business name and logo. Works in Portuguese, English, and Spanish, with a light or dark theme, and keeps everything stored locally on your computer.": "Organiza clientes e vendas em um só lugar, com pesquisa rápida e recibos que já saem com o nome e a logo do seu negócio. Funciona em Português, Inglês e Espanhol, com tema claro ou escuro, e guarda tudo localmente no seu computador.",
    "Preferences": "Preferências",
    "Auto": "Automático",
    "Dark": "Escuro",
    "Light": "Claro",
    "Next": "Próximo",
    "Back": "Voltar",
    "Skip": "Pular",
    "Finish": "Concluir",
    "Choose a folder…": "Escolha um diretório…",
    "Folder selection is only available in the desktop app.": "A seleção de diretórios está disponível apenas no aplicativo desktop.",
    "Error choosing the directory.": "Erro ao escolher o diretório.",
    "Selected folder (browser) — will be handled by the app": "Diretório selecionado (navegador) — será tratado pelo aplicativo desktop",
    "000000000": "000000000",

    # ===== Adições solicitadas (com correção Difference -> Discount) =====
    "Identification": "Identificação",
    "Value": "Valor",
    "Final Value": "Valor Final",
    "Discount": "Desconto",
    # ✅ ADIÇÃO (msgid usado pelo código)
    "receipt": "recibo",
    "PRODUCT": "Produto",
}

# ES fallback (full + extras)
_ES: dict[str, str] = {
    "Simple Sales Management": "Gestión Simple de Ventas",
    "Simple Sales Management (SSM)": "Gestión Simple de Ventas (SSM)",
    "SSM": "SSM",
    "Clients & Sales": "Clientes y Ventas",
    "Navigation": "Navegación",
    "Business": "Negocio",
    "New Sale": "Nueva Venta",
    "Sales": "Ventas",
    "Clients": "Clientes",
    "Business (Store Data)": "Negocio (Datos de la Tienda)",
    "Add": "Agregar",
    "Remove image": "Quitar imagen",
    "Logo": "Logo",
    "Store Name": "Nombre de la Tienda",
    "Phone": "Teléfono",
    "Postal Code": "Código Postal",
    "Address 2": "Dirección 2",
    "Tax ID": "ID Fiscal",
    "Save": "Guardar",
    "Save File": "Guardar archivo",
    "Select Directory": "Elegir directorio",
    "Open File": "Abrir archivo",
    "Images": "Imágenes",
    "All files": "Todos los archivos",
    # Página de negocio — genéricos
    "ACME Co.": "ACME Co.",
    "(00) 0000-0000": "(00) 0000-0000",
    "00000-000": "00000-000",
    "123 Main St": "Calle Principal 123",
    "City/State": "Ciudad/Provincia",
    "Client": "Cliente",
    "Code": "Código",
    "6 digits (e.g., 012345)": "6 dígitos (p. ej., 012345)",
    "Automatic": "Automático",
    "Show requirements": "Mostrar requisitos",
    "Generate code automatically": "Generar código automáticamente",
    "Requirements: exactly": "Requisitos: exactamente",
    "numeric digits (e.g., 012345).": "dígitos numéricos (p. ej., 012345).",
    "Name": "Nombre",
    "Address": "Dirección",
    "Contact": "Contacto",
    "Description": "Descripción",
    "Client name": "Nombre del cliente",
    "Street & number": "Calle y número",
    "Phone or email": "Teléfono o correo",
    "Client notes": "Notas del cliente",
    "Sale": "Venta",
    "Product": "Producto",
    # ✅ management.html, modo "sale" (Nova Venda)
    "Product or service": "Producto o servicio",
    "Only numbers": "Solo números",
    "Only numbers lower than the initial value.": "Solo números menores que el valor inicial.",
    # Moneda genérica (sin símbolos)
    "Amount": "Importe",
    "Details": "Detalles",
    "Sell": "Vender",
    "No Discount": "Sin Descuento",
    "Code must have exactly 6 digits.": "El código debe tener exactamente 6 dígitos.",
    "Invalid code.": "Código inválido.",
    "Filters": "Filtros",
    "Order": "Orden",
    "Ascending": "Ascendente",
    "Descending": "Descendente",
    "Month": "Mes",
    "All": "Todos",
    "Year": "Año",
    "List": "Lista",
    "Actions": "Acciones",
    "Search": "Buscar",
    "Select": "Seleccionar",
    "Back to top": "Volver arriba",
    "No data": "Sin datos",
    "No sales yet": "Aún sin ventas",
    "No clients yet": "Aún sin clientes",
    "Delete client": "Eliminar cliente",
    "Confirm deletion": "Confirmar eliminación",
    "Close": "Cerrar",
    "Delete": "Eliminar",
    "Cancel": "Cancelar",
    "(Blank)": "(Vacío)",
    "All sales from this client will be removed.": "Se eliminarán todas las ventas de este cliente.",
    "Failed to delete the client.": "Error al eliminar el cliente.",
    "Error deleting the client.": "Error eliminando el cliente.",
    "Subtotals": "Subtotales",
    "Gross": "Bruto",
    "Final": "Final",
    "Date/Time": "Fecha/Hora",
    # ✅ ADIÇÃO (msgid usado pelo código)
    "Date/Hour": "Fecha/Hora",
    "Print receipt": "Imprimir recibo",
    "Delete sale": "Eliminar venta",
    "This action is irreversible.": "Esta acción es irreversible.",
    "Failed to delete the sale.": "Fallo al eliminar la venta.",
    "Error deleting.": "Error al eliminar.",
    "Receipt": "Recibo",
    "Receipt image": "Imagen del recibo",
    "Generate PDF": "Generar PDF",
    "Could not generate the PDF.": "No se pudo generar el PDF.",
    "Failed to save the PDF.": "Fallo al guardar el PDF.",
    "Failed to generate the PDF.": "Fallo al generar el PDF.",
    "STORE NAME (e.g., Example Optics)": "NOMBRE DE LA TIENDA (p. ej., Tienda Ejemplo)",
    "PHONE: (00) 0000-0000": "TELÉFONO: (00) 0000-0000",
    "ADDRESS (e.g., Example St, 123)": "DIRECCIÓN (p. ej., Calle Ejemplo, 123)",
    "TAX ID: 00.000.000/0000-00": "ID FISCAL: 00.000.000/0000-00",
    "CLIENT": "CLIENTE",
    "CODE": "CÓDIGO",
    "NAME": "NOMBRE",
    "SALE": "VENTA",
    "DESCRIPTION": "DESCRIPCIÓN",
    "PRICE": "PRECIO",
    "ZIP": "C.P.",
    "TAX ID": "ID FISCAL",
    "Date:": "Fecha:",
    "Business data saved.": "Datos de la tienda guardados.",
    "Fill the product.": "Complete el producto.",
    "Name is required.": "El nombre es obligatorio.",
    "Enter a valid numeric amount (e.g., 1,234.56 or 1.234,56).": "Ingrese un importe numérico válido (p. ej., 1.234,56 o 1,234.56).",
    "Enter a valid final amount (e.g., 1,000.00) or keep 'No Discount'.": "Ingrese un importe final válido (p. ej., 1.000,00) o mantenga 'Sin Descuento'.",
    "Final amount cannot be greater than the original amount.": "El importe final no puede ser mayor que el importe original.",
    "With 'Automatic' checked, fill client and sale details.": "Con 'Automático' marcado, complete los datos del cliente y de la venta.",
    "Sale created successfully!": "¡Venta creada con éxito!",
    "Code must have exactly 6 digits (e.g., 012345).": "El código debe tener exactamente 6 dígitos (p. ej., 012345).",
    # Settings UI
    "Settings": "Configuración",
    "Appearance": "Apariencia",
    "Directories": "Directorios",
    "Language": "Idioma",
    "Theme": "Tema",
    "Data directory": "Directorio de datos",
    "Default PDF directory": "Directorio PDF predeterminado",
    "Explorer directory": "Directorio del Explorador",
    "Open": "Abrir",
    "Set to Downloads": "Usar Descargas",
    "Choose…": "Elegir…",
    "Settings saved.": "Configuración guardada.",
    # ✅ settings.html (nuevas keys)
    "Data": "Datos",
    "Export": "Exportar",
    "Import": "Importar",
    "Reset": "Restablecer",
    "Reset all data": "Restablecer todos los datos",
    "Deletes all sales, clients and business data and restarts the initial setup. This cannot be undone.": "Elimina todos los datos de ventas, clientes y negocio y reinicia la configuración inicial. Esto no se puede deshacer.",
    "Export generates a signed data.json that can only be imported by this app.": "La exportación genera un data.json firmado que solo puede importarse en esta app.",
    "Enter a valid directory.": "Ingrese un directorio válido.",
    "Could not open in Explorer.": "No se pudo abrir en el Explorador.",
    "Explorer integration is unavailable outside the desktop app.": "La integración con el Explorador no está disponible fuera de la app de escritorio.",
    "Error opening the directory.": "Error al abrir el directorio.",
    "Downloading…": "Descargando…",
    # Extras
    "Export spreadsheet": "Exportar planilla",
    "Print receipts": "Imprimir recibos",
    "Selected sales": "Ventas seleccionadas",
    "Nothing selected": "Nada seleccionado",
    "Spreadsheet": "Planilla",
    "Delete sales": "Eliminar ventas",
    "Confirm": "Confirmar",
    "Edit": "Editar",
    "Failed to save.": "Fallo al guardar.",
    "Failed to delete.": "Fallo al eliminar.",
    # Nombres base de archivos
    "clients_spreadsheet.csv": "planilla_clientes.csv",
    "sales_spreadsheet.csv": "planilla_ventas.csv",
    "Failed to generate spreadsheet.": "Fallo al generar la planilla.",
    # Feedback / compat
    "Saved": "Guardado",
    "Saving…": "Guardando…",
    "Failed to save": "Fallo al guardar",
    "Open in Explorer": "Abrir en el Explorador",
    # ===== Added for management.html (search bar + clients list) =====
    "Select all": "Seleccionar todo",
    "Selected count": "Cantidad seleccionada",
    "Print": "Imprimir",
    "Select items to export.": "Seleccione elementos para exportar.",
    "Select items to delete.": "Seleccione elementos para eliminar.",
    "Select an item in the list to edit.": "Seleccione un elemento en la lista para editar.",
    "Select a sale to print.": "Seleccione una venta para imprimir.",
    "Press ESC or Cancel to exit.": "Presione ESC o Cancelar para salir.",
    # ===== management.html, modo "sale" (Select Client popup + discount modal) =====
    "Register client": "Registrar cliente",
    "New client": "Nuevo cliente",
    "Tap to add": "Toque para agregar",
    "Change": "Cambiar",
    "Remove": "Quitar",
    "Apply": "Aplicar",
    "No clients": "Sin clientes",
    # ===== Added for welcome.html (first setup) =====
    "Welcome": "Bienvenido",
    "Set up your management to get started.": "Configure su gestión para empezar.",
    "Setup steps": "Pasos de configuración",
    "Introduction": "Introducción",
    "Keeps clients and sales in one place, with quick search and receipts that already carry your business name and logo. Works in Portuguese, English, and Spanish, with a light or dark theme, and keeps everything stored locally on your computer.": "Organiza clientes y ventas en un solo lugar, con búsqueda rápida y recibos que ya incluyen el nombre y el logo de su negocio. Funciona en Portugués, Inglés y Español, con tema claro u oscuro, y guarda todo localmente en su computadora.",
    "Preferences": "Preferencias",
    "Auto": "Automático",
    "Dark": "Oscuro",
    "Light": "Claro",
    "Next": "Siguiente",
    "Back": "Atrás",
    "Skip": "Omitir",
    "Finish": "Finalizar",
    "Choose a folder…": "Elija un directorio…",
    "Folder selection is only available in the desktop app.": "La selección de directorios solo está disponible en la app de escritorio.",
    "Error choosing the directory.": "Error al elegir el directorio.",
    "Selected folder (browser) — will be handled by the app": "Directorio seleccionado (navegador) — será gestionado por la app de escritorio",
    "000000000": "000000000",

    # ===== Adições solicitadas (com correção Difference -> Discount) =====
    "Identification": "Identificación",
    "Value": "Valor",
    "Final Value": "Valor final",
    "Discount": "Descuento",
    # ✅ ADIÇÃO (msgid usado pelo código)
    "receipt": "recibo",
    "PRODUCT": "Producto",
}


# --------------- Selection and translation ---------------
def _builtin_for(lang_key: str) -> dict[str, str]:
    if lang_key == "pt":
        return _PT
    if lang_key == "es":
        return _ES
    return {}


def _fallback_map(locale_str: str | None) -> dict[str, str]:
    key = _lang_key(locale_str)  # en/pt/es only
    base = _builtin_for(key)
    ext = _load_external(key)
    if not ext:
        return base
    merged = dict(base)
    merged.update(ext)
    return merged


def t(msgid: str, locale_str: str | None = None) -> str:
    if not isinstance(msgid, str):
        msgid = str(msgid)

    m = _fallback_map(locale_str)

    if msgid in m:
        out = m[msgid]
    else:
        cand = (msgid.title(), msgid.capitalize(), msgid.lower())
        out = next((m[c] for c in cand if c in m), msgid)

    if msgid.isupper():
        try:
            return out.upper()
        except Exception:
            return out
    return out