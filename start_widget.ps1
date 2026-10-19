$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectDir '.venv\Scripts\pythonw.exe'
$runner = Join-Path $projectDir 'run_widget.py'
$oldScript = Join-Path $projectDir 'hardware_widget.py'
$logDir = Join-Path $projectDir 'logs'

try {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    if (-not (Test-Path -LiteralPath $python)) {
        throw 'Ambiente Python não encontrado. Execute: py -m venv .venv; .\.venv\Scripts\python -m pip install -r requirements.txt'
    }
    if (-not (Test-Path -LiteralPath $runner)) {
        throw 'run_widget.py não encontrado na pasta do projeto.'
    }

    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        $arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $MyInvocation.MyCommand.Path + '"'
        Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden
        exit 0
    }

    # Restart only Python processes running this exact project, never other Python apps.
    try {
        Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
            Where-Object {
                $_.CommandLine -and
                ($_.CommandLine.Contains($runner) -or $_.CommandLine.Contains($oldScript))
            } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    } catch {
        Add-Content -LiteralPath (Join-Path $logDir 'launcher.log') `
            -Value ((Get-Date -Format s) + ' Não foi possível verificar instâncias antigas: ' + $_.Exception.Message)
    }

    Start-Process -FilePath $python -ArgumentList ('"' + $runner + '"') `
        -WorkingDirectory $projectDir -WindowStyle Hidden
    Add-Content -LiteralPath (Join-Path $logDir 'launcher.log') `
        -Value ((Get-Date -Format s) + ' Widget iniciado com privilégios de administrador.')
} catch {
    $message = (Get-Date -Format s) + ' Falha ao iniciar: ' + $_.Exception.Message
    Add-Content -LiteralPath (Join-Path $logDir 'launcher.log') -Value $message
    Write-Error $message
    exit 1
}
