@echo off
rem Top products by traffic + EU vendor check -> output\Топ продукти по трафик*.xlsx
rem   Double-click                       -> vendor check on the current traffic data (free)
rem   top_products.bat --traffic         -> also fetch the traffic data again from DataForSEO (paid, capped)
rem   top_products.bat --vendors "C:\path\EU vendors.zip"   -> a specific vendors file
chcp 65001 >nul
setlocal
cd /d "%~dp0"
python -m top_products %*
echo.
pause
