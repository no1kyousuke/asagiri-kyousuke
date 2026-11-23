#define MyAppName "SubFlow Studio - NVIDIA CUDA 12 GPU 加速扩展包"
#define MyAppVersion "9.2.0"
#define MyAppPublisher "SubFlow Studio"

[Setup]
AppId={{B8B62A62-0D83-4E0A-9587-7B6F6C4A28D3}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\SubFlow Studio
UsePreviousAppDir=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
CreateUninstallRegKey=no
UpdateUninstallLogAppName=no
OutputDir=installer
OutputBaseFilename=SubFlowStudio_GPU_Pack_Setup
SetupIconFile=assets\subflow.ico
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "dist_gpu_pack\nvidia\*"; DestDir: "{app}\nvidia"; Flags: ignoreversion recursesubdirs createallsubdirs
