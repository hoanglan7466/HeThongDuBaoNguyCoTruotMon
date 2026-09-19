[CmdletBinding()]
param(
    [int]$Port = 5000
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'

if (-not (Test-Path $Python)) {
    throw 'Không tìm thấy .venv. Hãy tạo môi trường bằng: py -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt'
}
if (-not (Test-Path (Join-Path $Root '.env'))) {
    throw 'Không tìm thấy .env. Hãy sao chép .env.example thành .env và cấu hình DATABASE_URL, SECRET_KEY.'
}

Set-Location $Root
& $Python -m flask --app run.py init-db
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$Model = Join-Path $Root 'machine_learning\models\random_forest.joblib'
if (-not (Test-Path $Model)) {
    & $Python -m flask --app run.py train-model
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host "Starting Flask at http://127.0.0.1:$Port"
& $Python -m flask --app run.py run --host 127.0.0.1 --port $Port
