@echo off
rem Top products by traffic + EU vendor check -> output\Топ продукти по трафик*.xlsx
rem   Double-click                       -> scrape all shops + check every vendor in the Google Drive "EU vendors" folder
rem                                         (DataForSEO: only the first run of a month pays, about $6, capped)
rem   top_products.bat --offline         -> no DataForSEO: reuse the traffic data in the Excel (free)
rem   top_products.bat --vendors "C:\path\EU vendors"   -> a specific vendors folder or .zip
chcp 65001 >nul
setlocal
cd /d "%~dp0"
python -m top_products %*
echo.
pause
