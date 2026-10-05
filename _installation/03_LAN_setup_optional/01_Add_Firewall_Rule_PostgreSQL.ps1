# Plato's Disciple - PostgreSQL LAN firewall setup
#
# Purpose:
#   Allow other computers on the LAN to reach PostgreSQL on TCP port 5432.
#   Single-computer installations using localhost can skip this script.
#
# How to run:
#   1. Open PowerShell as Administrator on the PostgreSQL server.
#   2. If script execution is blocked, run:
#      Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   3. Run this script.
#
# Security:
#   The rule applies only to Windows Domain and Private network profiles.
#   It does not enable PostgreSQL access on Public network profiles.

$ErrorActionPreference = "Stop"

$RuleName = "Plato's Disciple - PostgreSQL TCP 5432"
$Port = 5432

# Remove only the rule created by this script, so rerunning is safe.
Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule

# Allow inbound PostgreSQL traffic on trusted Windows network profiles.
New-NetFirewallRule `
    -DisplayName $RuleName `
    -Direction Inbound `
    -Protocol TCP `
    -LocalPort $Port `
    -Action Allow `
    -Profile Domain,Private | Out-Null

Write-Host "Firewall rule created: $RuleName" -ForegroundColor Green

# Display the resulting port rule for verification.
Get-NetFirewallRule -DisplayName $RuleName |
    Get-NetFirewallPortFilter
