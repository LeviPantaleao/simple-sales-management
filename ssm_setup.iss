; =====================================================================
;  SSM — Simple Sales Management — Instalador (Inno Setup 6+)
;  Versao 0.0.1
;
;  - Idiomas do setup: Ingles, Portugues (BR), Espanhol
;  - Nome do programa instalado (atalhos, Menu Iniciar, Adicionar/Remover
;    Programas, E os metadados do proprio SSM.exe) e LOCALIZADO conforme
;    o idioma escolhido no setup.
;  - Grava ssm_install_lang.txt para o app abrir no idioma do setup.
;
;  Pre-requisito: build PyInstaller feito em dist\ssm\ (com build_ssm.ps1,
;  que ja renomeia electron.exe -> SSM.exe e gera resources\app).
;
;  IMPORTANTE: coloque o rcedit-x64.exe em C:\ssm\tools\rcedit-x64.exe
;  antes de compilar este .iss (o instalador o embute e usa durante a
;  instalacao para gravar o nome localizado nos metadados do SSM.exe).
;  Ja esta instalado globalmente via npm; copie de:
;    %APPDATA%\npm\node_modules\rcedit\bin\rcedit-x64.exe
;
;  Compilar: abrir no Inno Setup Compiler > Compile   (ou: iscc ssm_setup.iss)
; =====================================================================

#define MyAppVersion "0.0.1"
#define MyAppPublisher "Levi Pantaleão"
#define MyAppExeName "ssm.exe"
; GUID proprio do SSM (NAO reutilizar no BSM)
#define MyAppId "{{BC0AC4E8-E10E-453F-91F5-39C728B0EA80}"
#define BuildDir "dist\ssm"

[Setup]
AppId={#MyAppId}
AppName={cm:AppName}
; ✅ Sem número de versão nos textos do instalador (título do wizard,
; Adicionar/Remover Programas etc.) — é uma tecnicidade de build, não algo
; pro usuário final ver. AppVersion/VersionInfoVersion continuam definidos
; (o Windows exige e usa internamente), só não aparecem concatenados aqui.
AppVerName={cm:AppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}
DefaultDirName={autopf}\{cm:AppName}
DefaultGroupName={cm:AppName}
UninstallDisplayName={cm:AppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=installer_output
OutputBaseFilename=SSM-Setup
SetupIconFile=app.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
ShowLanguageDialog=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
CloseApplications=yes

[Languages]
Name: "en";   MessagesFile: "compiler:Default.isl"
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "es";   MessagesFile: "compiler:Languages\Spanish.isl"

; Nome do programa traduzido por idioma -- usado nos atalhos E nos
; metadados do SSM.exe (via rcedit, no [Code] abaixo).
[CustomMessages]
en.AppName=Simple Sales Management
ptbr.AppName=Gestor Simples de Vendas
es.AppName=Gestion Simple de Ventas

en.AppDescription=Simple Sales Management
ptbr.AppDescription=Gestor Simples de Vendas
es.AppDescription=Gestion Simple de Ventas

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; rcedit embutido no instalador (nao fica no diretorio de instalacao final)
Source: "tools\rcedit-x64.exe"; DestDir: "{tmp}"; Flags: dontcopy

[Icons]
Name: "{group}\{cm:AppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\app.ico"
Name: "{group}\{cm:UninstallProgram,{cm:AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{cm:AppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\app.ico"; Tasks: desktopicon

; Firewall: bloqueia QUALQUER acesso de entrada aos executaveis do SSM.
; O backend so faz bind em 127.0.0.1 (loopback nao passa pelo firewall),
; entao na pratica isto e uma rede de seguranca: garante que nem uma
; configuracao errada (bind em 0.0.0.0) exponha o app na rede local, e
; suprime qualquer prompt "permitir acesso?" do Windows.
[Run]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""SSM - block inbound (launcher)"" dir=in action=block program=""{app}\{#MyAppExeName}"" enable=yes profile=any"; Flags: runhidden
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""SSM - block inbound (electron)"" dir=in action=block program=""{app}\_internal\node_modules\electron\dist\SSM.exe"" enable=yes profile=any"; Flags: runhidden
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{cm:AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""SSM - block inbound (launcher)"""; Flags: runhidden; RunOnceId: "DelFwLauncher"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""SSM - block inbound (electron)"""; Flags: runhidden; RunOnceId: "DelFwElectron"

[UninstallDelete]
Type: files; Name: "{app}\ssm_install_lang.txt"
Type: files; Name: "{app}\_internal\ssm_install_lang.txt"

[Code]
function SeedLangTag(): String;
begin
  case ActiveLanguage() of
    'ptbr': Result := 'pt-BR';
    'es':   Result := 'es';
  else
    Result := 'en';
  end;
end;

// Valor no formato usado DENTRO do settings.json (ex.: "pt_BR", nao "pt-BR")
function SettingsLocaleValue(): String;
begin
  case ActiveLanguage() of
    'ptbr': Result := 'pt_BR';
    'es':   Result := 'es';
  else
    Result := 'en';
  end;
end;

procedure SaveSeedFile(const FileName, Tag: String);
begin
  if not SaveStringToFile(FileName, Tag + #13#10, False) then
    Log('SSM: falha ao gravar seed em ' + FileName);
end;

// PosEx nao existe no Pascal Script do Inno Setup -- implementacao propria
// usando apenas Pos/Copy (garantidamente suportadas).
function PosFrom(const SubStr, S: String; Offset: Integer): Integer;
var
  Sub: String;
  P: Integer;
begin
  Result := 0;
  if Offset < 1 then Offset := 1;
  if Offset > Length(S) then Exit;
  Sub := Copy(S, Offset, Length(S) - Offset + 1);
  P := Pos(SubStr, Sub);
  if P > 0 then
    Result := P + Offset - 1;
end;

// Substitui o valor de uma chave string simples ("chave": "valor") em um
// JSON de uma linha/bloco conhecido, preservando todo o resto do arquivo
// intacto (nao faz parsing completo de JSON -- so localiza as aspas ao
// redor do valor da chave e troca o conteudo entre elas).
function ReplaceJsonStringValue(const Content, Key, NewValue: String): String;
var
  KeyToken: String;
  KeyPos, ColonPos, Q1, Q2: Integer;
begin
  Result := Content;
  KeyToken := '"' + Key + '"';
  KeyPos := Pos(KeyToken, Content);
  if KeyPos = 0 then Exit;

  ColonPos := PosFrom(':', Content, KeyPos);
  if ColonPos = 0 then Exit;

  Q1 := PosFrom('"', Content, ColonPos + 1);
  if Q1 = 0 then Exit;
  Q2 := PosFrom('"', Content, Q1 + 1);
  if Q2 = 0 then Exit;

  Result := Copy(Content, 1, Q1) + NewValue + Copy(Content, Q2, Length(Content) - Q2 + 1);
end;

// Atualiza o preferred_locale de um settings.json JA EXISTENTE (dados de
// instalacoes/testes anteriores), para que o idioma escolhido NESTE setup
// sempre prevaleca -- mesmo quando ha dados antigos de uma sessao anterior
// em outro idioma. Se o arquivo nao existir, nao faz nada (silencioso).
procedure PatchPreferredLocaleInFile(const FileName, NewValue: String);
var
  Content: AnsiString;
  NewContent: String;
begin
  if not FileExists(FileName) then Exit;
  if not LoadStringFromFile(FileName, Content) then
  begin
    Log('SSM: falha ao ler ' + FileName + ' para atualizar preferred_locale.');
    Exit;
  end;

  NewContent := ReplaceJsonStringValue(String(Content), 'preferred_locale', NewValue);
  if NewContent = String(Content) then
  begin
    Log('SSM: preferred_locale nao encontrado/alterado em ' + FileName);
    Exit;
  end;

  if SaveStringToFile(FileName, NewContent, False) then
    Log('SSM: preferred_locale atualizado em ' + FileName + ' -> ' + NewValue)
  else
    Log('SSM: falha ao salvar ' + FileName + ' apos atualizar preferred_locale.');
end;

// Verifica os locais mais comuns onde o settings.json pode existir (de
// instalacoes/testes anteriores) e atualiza o idioma em todos que existirem.
procedure PatchAllKnownSettingsFiles(const NewValue: String);
begin
  PatchPreferredLocaleInFile(ExpandConstant('{userdocs}\SimpleSalesManagement\data\settings.json'), NewValue);
  PatchPreferredLocaleInFile(ExpandConstant('{localappdata}\SimpleSalesManagement\data\settings.json'), NewValue);
end;

// Grava o nome LOCALIZADO (idioma escolhido no setup) diretamente nos
// metadados do SSM.exe instalado (ProductName/FileDescription), usando
// o rcedit embutido no instalador. Isso garante que qualquer superficie
// do Windows que leia esses metadados (Gerenciador de Tarefas, Propriedades
// do arquivo, e o menu da barra de tarefas quando o SSM.exe e aberto sem
// passar pelo launcher) mostre o nome no idioma certo.
procedure LocalizeExeMetadata();
var
  RceditPath: String;
  SsmExePath: String;
  IcoPath: String;
  AppNameLocalized: String;
  Params: String;
  ResultCode: Integer;
begin
  SsmExePath := ExpandConstant('{app}\_internal\node_modules\electron\dist\SSM.exe');
  if not FileExists(SsmExePath) then
  begin
    Log('SSM: SSM.exe nao encontrado em ' + SsmExePath + ' -- pulando localizacao de metadados.');
    Exit;
  end;

  ExtractTemporaryFile('rcedit-x64.exe');
  RceditPath := ExpandConstant('{tmp}\rcedit-x64.exe');
  if not FileExists(RceditPath) then
  begin
    Log('SSM: rcedit-x64.exe nao encontrado apos extracao -- pulando localizacao de metadados.');
    Exit;
  end;

  AppNameLocalized := CustomMessage('AppName');

  // IMPORTANTE: --set-icon precisa ir NA MESMA chamada dos --set-version-string.
  // Rodar o rcedit em duas chamadas separadas sobre o mesmo exe (uma so com
  // icone, outra so com strings) pode fazer um dos grupos de icone internos
  // do binario do Electron voltar ao padrao (o "atomo" do Electron) --
  // por isso tudo e gravado de uma vez so aqui.
  IcoPath := ExpandConstant('{app}\app.ico');

  Params := '"' + SsmExePath + '"';
  if FileExists(IcoPath) then
    Params := Params + ' --set-icon "' + IcoPath + '"';
  Params := Params +
    ' --set-version-string "ProductName" "' + AppNameLocalized + '"' +
    ' --set-version-string "FileDescription" "' + AppNameLocalized + '"' +
    ' --set-version-string "InternalName" "SSM"' +
    ' --set-version-string "OriginalFilename" "SSM.exe"';

  Log('SSM: executando rcedit para localizar metadados + icone (' + AppNameLocalized + ')');
  if not Exec(RceditPath, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    Log('SSM: falha ao executar rcedit (erro do sistema).')
  else
    Log('SSM: rcedit concluido com codigo ' + IntToStr(ResultCode));
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Tag: String;
begin
  if CurStep = ssPostInstall then
  begin
    Tag := SeedLangTag();
    Log('SSM: gravando seed de idioma = ' + Tag);
    SaveSeedFile(ExpandConstant('{app}\ssm_install_lang.txt'), Tag);
    if DirExists(ExpandConstant('{app}\_internal')) then
      SaveSeedFile(ExpandConstant('{app}\_internal\ssm_install_lang.txt'), Tag);

    // Garante que o idioma escolhido AGORA no setup prevaleça mesmo se
    // já existirem dados de uma instalação/teste anterior em outro idioma.
    PatchAllKnownSettingsFiles(SettingsLocaleValue());

    LocalizeExeMetadata();
  end;
end;
