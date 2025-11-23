# # --- REPLACE THESE PLACEHOLDERS WITH YOUR ACTUAL POCKET OPTION CREDENTIALS ---
# # Helper: Read a key from .env, return the value with surrounding quotes and whitespace removed
# function Get-DotEnvValue {
# 	param(
# 		[Parameter(Mandatory=$true)]
# 		[string] $Key,
# 		[string] $EnvFile = ".env"
# 	)

# 	if (-not (Test-Path $EnvFile)) { return $null }

# 	$line = Get-Content $EnvFile | Where-Object { $_ -match "^\s*$Key\s*=" } | Select-Object -First 1
# 	if (-not $line) { return $null }

# 	# Split on first '=' to allow '=' inside the value
# 	$parts = $line -split '=', 2
# 	if ($parts.Count -lt 2) { return $null }

# 	$value = $parts[1].Trim()

# 	# Remove surrounding single/double quotes and surrounding whitespace
# 	$value = $value -replace "^[\s\"']+", ""
# 	$value = $value -replace "[\s\"']+$", ""

# 	return $value
# }

# $poEmail = Get-DotEnvValue -Key "PO_EMAIL"
# $poPassword = Get-DotEnvValue -Key "PO_PASSWORD"
$poEmail = "got1joke@gmail.com"
$poPassword = "0nly!P@5s@PO"
# -----------------------------------------------------------------------------

# Set environment variables for the current PowerShell session
$env:PO_EMAIL = $poEmail
$env:PO_PASSWORD = $poPassword
$env:SSID_REFRESH_INTERVAL_MINUTES = "86400" # Optional: Set refresh interval (default 30 mins)

# Navigate to the directory where your Python scripts are located
# Adjust this path if your scripts are not in the same directory as this .ps1 file
# Set-Location -Path (Split-Path -Parent $MyInvocation.MyCommand.Definition)

# Run the Python scraper script
# You might need to specify the full path to your python executable if it's not in your PATH
uv run scraper.py

# Optional: Clear environment variables after the script finishes (or after closing the terminal)
# Remove-Item Env:\PO_EMAIL
# Remove-Item Env:\PO_PASSWORD
