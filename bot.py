import os
import json
from io import BytesIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import qrcode

# 🔐 Configurações por variáveis de ambiente (Railway/GitHub)
TOKEN = os.environ.get("TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
PIX_KEY = os.environ.get("PIX_KEY", "")
NOME_RECEBEDOR = os.environ.get("NOME_RECEBEDOR", "Seu Nome")[:25]
CIDADE_RECEBEDOR = os.environ.get("CIDADE_RECEBEDOR", "BRASIL")[:15]

if not TOKEN or ADMIN_ID == 0:
    raise RuntimeError("⚠ Defina as variáveis de ambiente TOKEN e ADMIN_ID antes de rodar.")

# Planos
PLANOS = {
    "mensal": 10.00,
    "trimestral": 25.00,
    "vitalicio": 40.00
}

# Persistência de usuários pendentes
PENDENTES_FILE = "usuarios_pendentes.json"
usuarios_pendentes = {}

def load_pendentes():
    global usuarios_pendentes
    try:
        if os.path.exists(PENDENTES_FILE):
            with open(PENDENTES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                usuarios_pendentes = {int(k): v for k, v in data.items()}
    except Exception:
        usuarios_pendentes = {}

def save_pendentes():
    try:
        with open(PENDENTES_FILE, "w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in usuarios_pendentes.items()}, f, ensure_ascii=False)
    except Exception:
        pass

# Funções auxiliares para gerar payload PIX (BR Code)
def monta_campo(idc, valor):
    return idc + f"{len(valor):02d}" + valor

def crc16(payload):
    polinomio = 0x1021
    resultado = 0xFFFF
    for byte in payload.encode("utf-8"):
        resultado ^= (byte << 8)
        for _ in range(8):
            if (resultado & 0x8000):
                resultado = ((resultado << 1) ^ polinomio) & 0xFFFF
            else:
                resultado = (resultado << 1) & 0xFFFF
    return format(resultado, "04X")

def gerar_payload(chave_pix, nome, cidade, valor, txid="TXID"):
    gui = monta_campo("00", "BR.GOV.BCB.PIX")
    chave = monta_campo("01", chave_pix)
    merchant_account = monta_campo("26", gui + chave)
    merchant_category = monta_campo("52", "0000")
    moeda = monta_campo("53", "986")
    valor_field = monta_campo("54", f"{valor:.2f}")
    pais = monta_campo("58", "BR")
    nome_f = monta_campo("59", nome[:25])
    cidade_f = monta_campo("60", cidade[:15])
    txid_field = monta_campo("05", txid)
    adicional = monta_campo("62", txid_field)

    payload_sem_crc = (
        monta_campo("00", "01") +
        merchant_account +
        merchant_category +
        moeda +
        valor_field +
        pais +
        nome_f +
        cidade_f +
        adicional +
        "6304"
    )
    crc = crc16(payload_sem_crc)
    return payload_sem_crc + crc

# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("💳 Mensal - R$ 10", callback_data="mensal")],
        [InlineKeyboardButton("💳 Trimestral - R$ 25", callback_data="trimestral")],
        [InlineKeyboardButton("💳 Vitalício - R$ 40", callback_data="vitalicio")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Escolha seu plano:", reply_markup=reply_markup)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    plano = query.data
    user = query.from_user

    usuarios_pendentes[user.id] = plano
    save_pendentes()

    valor = PLANOS.get(plano, 0.00)
    txid = f"{user.id}-{plano}"
    payload = gerar_payload(PIX_KEY, NOME_RECEBEDOR, CIDADE_RECEBEDOR, valor, txid=txid)

    # Gerar QR Code
    qr = qrcode.make(payload)
    bio = BytesIO()
    bio.name = "pix.png"
    qr.save(bio, "PNG")
    bio.seek(0)

    keyboard = [[InlineKeyboardButton("✅ Já paguei", callback_data=f"pago_{plano}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    msg = (
        f"Você escolheu o plano <b>{plano.upper()}</b> (R$ {valor:.2f}).\n\n"
        f"💳 Copie e cole no app bancário:\n\n"
        f"<code>{payload}</code>\n\n"
        f"Ou escaneie o QR que enviei.\n\n"
        f"Depois clique em <b>Já paguei</b>."
    )

    if query.message:
        await query.message.edit_text(msg, parse_mode="HTML", reply_markup=reply_markup)

    await context.bot.send_photo(chat_id=user.id, photo=bio, caption="📸 QR Code para pagamento PIX")

async def confirmar_pagamento(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    plano = query.data.split("_")[1]
    user = query.from_user

    msg_admin = (
        f"⚠ Novo pagamento pendente!\n\n"
        f"👤 Usuário: <b>{user.first_name}</b> (id <code>{user.id}</code>)\n"
        f"📦 Plano: <b>{plano}</b>\n\n"
        f"Use: <code>/liberar {user.id} {plano}</code>"
    )
    await context.bot.send_message(ADMIN_ID, msg_admin, parse_mode="HTML")

    if query.message:
        await query.message.edit_text("Seu pagamento foi enviado para verificação. ✅", parse_mode="HTML")

async def liberar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("Você não é administrador!")
    try:
        user_id = int(context.args[0])
        plano = context.args[1]
        await context.bot.send_message(user_id, f"✅ Seu pagamento do plano <b>{plano.upper()}</b> foi aprovado!", parse_mode="HTML")
        await update.message.reply_text(f"Usuário <code>{user_id}</code> liberado no plano <b>{plano}</b>.", parse_mode="HTML")
        usuarios_pendentes.pop(user_id, None)
        save_pendentes()
    except Exception:
        await update.message.reply_text("Erro! Use: /liberar <id_do_usuario> <plano>")

def main():
    load_pendentes()
    app = (
        Application.builder()
        .token(TOKEN)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(30)
        .pool_timeout(30)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("liberar", liberar))
    app.add_handler(CallbackQueryHandler(button, pattern="^(mensal|trimestral|vitalicio)$"))
    app.add_handler(CallbackQueryHandler(confirmar_pagamento, pattern="^pago_"))

    print("Bot rodando...")
    app.run_polling()

if __name__ == "__main__":
    main()
