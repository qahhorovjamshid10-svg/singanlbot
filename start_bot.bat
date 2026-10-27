@echo off
chcp 65001 >nul
echo ============================================================
echo   XAUUSD (Gold) Telegram AI Signal Bot — Ishga tushirish
echo ============================================================

if not exist ".env" (
    echo [!] .env fayli topilmadi, .env.example dan nusxa olinmoqda...
    copy ".env.example" ".env" >nul
    echo [!] Iltimos, .env fayli ichiga TELEGRAM_BOT_TOKEN qiymatini kiriting!
)

python -X utf8 main.py --mode run
pause
