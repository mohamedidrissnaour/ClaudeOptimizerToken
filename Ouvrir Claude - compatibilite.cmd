@echo off
setlocal
title Claude Desktop - compatibilite accessibilite
powershell.exe -NoProfile -Command "$r=Join-Path $env:LOCALAPPDATA 'AnthropicClaude'; if(Get-Process Claude -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($r + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) }) { Write-Host 'Quitte Claude Desktop depuis son icone dans la zone de notification, puis relance ce fichier.'; exit 2 }; $p=Get-ChildItem -LiteralPath $r -Directory -Filter 'app-*' -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^app-\d+(\.\d+){1,3}$' } | Sort-Object { [version] $_.Name.Substring(4) } -Descending | ForEach-Object { Join-Path $_.FullName 'claude.exe' } | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1; if(-not $p) { $p=Join-Path $r 'claude.exe' }; if(-not (Test-Path -LiteralPath $p -PathType Leaf)) { Write-Host 'Installation Claude Desktop introuvable.'; exit 3 }; Start-Process -FilePath $p -ArgumentList '--force-renderer-accessibility=complete','--enable-features=UiaProvider'"
if errorlevel 1 pause
endlocal
