# Get the path to requirements.txt
$requirementsPath = "requirements.txt"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv command not found. Please install uv first."
    exit 1
}
# Check if requirements.txt exists
if (Test-Path $requirementsPath) {
    Write-Host "Installing packages from requirements.txt..."
    uv add -r $requirementsPath
} else {
    Write-Host "requirements.txt not found. Please ensure the file exists in the current directory."
}