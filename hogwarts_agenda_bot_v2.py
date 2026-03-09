import os
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, List

import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Defina a variável de ambiente DISCORD_TOKEN antes de iniciar o bot.")

GUILD_ID = int(os.getenv("DEFAULT_GUILD_ID", "0")) or None
CANAL_DIRETORIA_ID = int(os.getenv("CANAL_DIRETORIA_ID", "0")) or None
ANTECEDENCIA_PADRAO = int(os.getenv("ANTECEDENCIA_PADRAO", "10"))
DB_PATH = os.getenv("AGENDA_DB_PATH", "agenda_hogwarts.db")
STAFF_ROLES = [r.strip() for r in os.getenv("STAFF_ROLES", "Monitor,Professor,Diretor,Funcionário").split(",") if r.strip()]
BOT_PERSONA = "Mispy"
SYSTEM_NAME = "Agenda Mágica"
DATE_FMT_INPUT = "%d/%m/%Y %H:%M"
DATE_FMT_DB = "%Y-%m-%d %H:%M:%S"
FUSO_BRASILIA = ZoneInfo("America/Sao_Paulo")


def now_brasilia() -> datetime:
    return datetime.now(FUSO_BRASILIA)


def to_db_dt(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=FUSO_BRASILIA)
    return dt.astimezone(FUSO_BRASILIA).isoformat(timespec="seconds")


def from_db_dt(value: str) -> datetime:
    value = value.strip()
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        dt = datetime.strptime(value, DATE_FMT_DB)
        dt = dt.replace(tzinfo=FUSO_BRASILIA)
    else:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=FUSO_BRASILIA)
    return dt.astimezone(FUSO_BRASILIA)

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


# =========================
# Banco de dados
# =========================
class Database:
    def __init__(self, path: str):
        self.path = path
        self._setup()

    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _setup(self):
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS registros (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    titulo TEXT NOT NULL,
                    descricao TEXT,
                    starts_at TEXT NOT NULL,
                    voice_channel_id INTEGER,
                    role_id INTEGER,
                    participant_ids TEXT,
                    created_by INTEGER NOT NULL,
                    notify_before INTEGER DEFAULT 10,
                    notify_sent INTEGER DEFAULT 0,
                    start_sent INTEGER DEFAULT 0,
                    canceled INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def create_record(
        self,
        *,
        kind: str,
        titulo: str,
        descricao: str,
        starts_at: datetime,
        voice_channel_id: Optional[int],
        role_id: Optional[int],
        participant_ids: Optional[List[int]],
        created_by: int,
        notify_before: int,
    ) -> int:
        now = to_db_dt(now_brasilia())
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO registros (
                    kind, titulo, descricao, starts_at, voice_channel_id,
                    role_id, participant_ids, created_by, notify_before,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    kind,
                    titulo,
                    descricao,
                    to_db_dt(starts_at),
                    voice_channel_id,
                    role_id,
                    ",".join(map(str, participant_ids or [])),
                    created_by,
                    notify_before,
                    now,
                    now,
                ),
            )
            return cur.lastrowid

    def update_record(
        self,
        record_id: int,
        *,
        titulo: Optional[str] = None,
        descricao: Optional[str] = None,
        starts_at: Optional[datetime] = None,
        voice_channel_id: Optional[int] = None,
        notify_before: Optional[int] = None,
    ):
        fields = []
        values = []
        if titulo is not None:
            fields.append("titulo = ?")
            values.append(titulo)
        if descricao is not None:
            fields.append("descricao = ?")
            values.append(descricao)
        if starts_at is not None:
            fields.append("starts_at = ?")
            values.append(to_db_dt(starts_at))
            fields.append("notify_sent = 0")
            fields.append("start_sent = 0")
        if voice_channel_id is not None:
            fields.append("voice_channel_id = ?")
            values.append(voice_channel_id)
        if notify_before is not None:
            fields.append("notify_before = ?")
            values.append(notify_before)
        fields.append("updated_at = ?")
        values.append(to_db_dt(now_brasilia()))
        values.append(record_id)
        with self._conn() as conn:
            conn.execute(f"UPDATE registros SET {', '.join(fields)} WHERE id = ?", values)

    def cancel_record(self, record_id: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE registros SET canceled = 1, updated_at = ? WHERE id = ?",
                (to_db_dt(now_brasilia()), record_id),
            )

    def get_record(self, record_id: int):
        with self._conn() as conn:
            cur = conn.execute("SELECT * FROM registros WHERE id = ?", (record_id,))
            return cur.fetchone()

    def upcoming_for_user(self, user_id: int):
        now = to_db_dt(now_brasilia())
        with self._conn() as conn:
            cur = conn.execute(
                """
                SELECT * FROM registros
                WHERE canceled = 0 AND starts_at >= ?
                  AND (participant_ids LIKE ? OR participant_ids LIKE ? OR participant_ids LIKE ? OR participant_ids = ?)
                ORDER BY starts_at ASC
                LIMIT 25
                """,
                (now, f"{user_id},%", f"%,{user_id},%", f"%,{user_id}", str(user_id)),
            )
            return cur.fetchall()

    def today_for_user(self, user_id: int):
        start = now_brasilia().replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        with self._conn() as conn:
            cur = conn.execute(
                """
                SELECT * FROM registros
                WHERE canceled = 0 AND starts_at >= ? AND starts_at < ?
                  AND (participant_ids LIKE ? OR participant_ids LIKE ? OR participant_ids LIKE ? OR participant_ids = ?)
                ORDER BY starts_at ASC
                """,
                (
                    to_db_dt(start),
                    to_db_dt(end),
                    f"{user_id},%",
                    f"%,{user_id},%",
                    f"%,{user_id}",
                    str(user_id),
                ),
            )
            return cur.fetchall()

    def upcoming_staff(self):
        now = to_db_dt(now_brasilia())
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT * FROM registros WHERE canceled = 0 AND starts_at >= ? ORDER BY starts_at ASC LIMIT 25",
                (now,),
            )
            return cur.fetchall()

    def autocomplete_records(self):
        now = to_db_dt(now_brasilia() - timedelta(days=1))
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT * FROM registros WHERE canceled = 0 AND starts_at >= ? ORDER BY starts_at ASC LIMIT 25",
                (now,),
            )
            return cur.fetchall()

    def pending_notifications(self):
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT * FROM registros WHERE canceled = 0 AND (notify_sent = 0 OR start_sent = 0)"
            )
            return cur.fetchall()

    def delete_old(self):
        cutoff = to_db_dt(now_brasilia() - timedelta(days=1))
        with self._conn() as conn:
            conn.execute("DELETE FROM registros WHERE starts_at < ?", (cutoff,))


db = Database(DB_PATH)


# =========================
# Utilitários
# =========================
def is_staff(member: discord.Member) -> bool:
    return any(role.name in STAFF_ROLES for role in member.roles)


def parse_datetime(data: str, hora: str) -> datetime:
    return datetime.strptime(f"{data} {hora}", "%d/%m/%Y %H:%M").replace(tzinfo=FUSO_BRASILIA)


def format_dt(dt_str: str) -> str:
    return from_db_dt(dt_str).strftime("%d/%m/%Y às %H:%M")


def voice_mention(guild: discord.Guild, voice_channel_id: Optional[int]) -> str:
    if not voice_channel_id:
        return "Não informado"
    ch = guild.get_channel(voice_channel_id)
    return ch.mention if ch else "Canal não encontrado"


def record_label(row: sqlite3.Row) -> str:
    dt = from_db_dt(row["starts_at"]).strftime("%d/%m %H:%M")
    return f"{row['titulo']} • {dt}"


async def send_dm_safe(member: discord.Member, embed: discord.Embed):
    try:
        await member.send(embed=embed)
        return True
    except Exception:
        return False


def build_reminder_embed(title: str, description: str, when: str, guild: discord.Guild, voice_id: Optional[int]) -> discord.Embed:
    embed = discord.Embed(
        title="🦉 Sua coruja trouxe um lembrete",
        description=description,
        color=discord.Color.gold(),
    )
    embed.add_field(name="Compromisso", value=title, inline=False)
    embed.add_field(name="Quando", value=when, inline=True)
    embed.add_field(name="Canal de voz", value=voice_mention(guild, voice_id), inline=True)
    embed.set_footer(text=f"{BOT_PERSONA} cuida da {SYSTEM_NAME} de Hogwarts")
    return embed


async def notify_record(row: sqlite3.Row, guild: discord.Guild, *, started: bool = False):
    when = format_dt(row["starts_at"])
    title = row["titulo"]
    voice_id = row["voice_channel_id"]

    if row["kind"] == "aula" and row["role_id"]:
        role = guild.get_role(int(row["role_id"]))
        members = [m for m in guild.members if role in m.roles] if role else []
        text = (
            f"{BOT_PERSONA} avisa que uma aula da {SYSTEM_NAME} {'está começando agora' if started else 'vai começar em breve'}!"
        )
        for m in members:
            embed = build_reminder_embed(title, text, when, guild, voice_id)
            await send_dm_safe(m, embed)
    else:
        ids = [int(x) for x in (row["participant_ids"] or "").split(",") if x.strip()]
        for uid in ids:
            member = guild.get_member(uid)
            if member:
                text = (
                    f"{BOT_PERSONA} avisa que um compromisso da {SYSTEM_NAME} {'está começando agora' if started else 'vai começar em breve'}."
                )
                embed = build_reminder_embed(title, text, when, guild, voice_id)
                await send_dm_safe(member, embed)


async def staff_check(interaction: discord.Interaction) -> bool:
    member = interaction.user
    if not isinstance(member, discord.Member) or not is_staff(member):
        await interaction.response.send_message(
            f"🧹 {BOT_PERSONA} pede desculpas, mas apenas **Monitor, Professor, Diretor e Funcionário** podem usar este comando.",
            ephemeral=True,
        )
        return False
    return True


async def record_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    rows = db.autocomplete_records()
    choices = []
    for row in rows:
        label = record_label(row)
        if current.lower() in label.lower():
            choices.append(app_commands.Choice(name=label[:100], value=str(row["id"])))
    return choices[:25]


# =========================
# Comandos
# =========================
@bot.tree.command(name="ajuda_agenda", description="Mispy explica como usar a Agenda Mágica")
async def ajuda_agenda(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🧹 Como usar a Agenda Mágica",
        description=(
            f"{BOT_PERSONA} diz que a **{SYSTEM_NAME}** organiza aulas, reuniões e compromissos em Hogwarts.\n\n"
            "**Comandos da moderação**\n"
            "`/aula_criar` cria um lembrete de aula para um cargo.\n"
            "`/reuniao_criar` agenda um encontro entre pessoas específicas.\n"
            "`/registro_editar` altera data, horário, canal ou detalhes.\n"
            "`/registro_cancelar` cancela um registro.\n"
            "`/agenda_staff` mostra os próximos registros da staff.\n\n"
            "**Comandos dos membros**\n"
            "`/minha_agenda` mostra seus próximos compromissos.\n"
            "`/agenda_hoje` mostra o que você tem hoje.\n\n"
            f"**Observação de {BOT_PERSONA}**\n"
            "Mispy limpa eventos passados automaticamente para manter a agenda organizada."
        ),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"{BOT_PERSONA} está feliz em ajudar!")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="aula_criar", description="Criar lembrete de aula para um cargo")
@app_commands.describe(
    titulo="Nome da aula",
    cargo="Cargo que receberá a aula",
    data="Data no formato DD/MM/AAAA",
    hora="Hora no formato HH:MM",
    canal_voz="Canal de voz da aula",
    descricao="Detalhes opcionais",
)
async def aula_criar(
    interaction: discord.Interaction,
    titulo: str,
    cargo: discord.Role,
    data: str,
    hora: str,
    canal_voz: discord.VoiceChannel,
    descricao: Optional[str] = None,
):
    if not await staff_check(interaction):
        return
    try:
        dt = parse_datetime(data, hora)
    except ValueError:
        await interaction.response.send_message("Use a data como **DD/MM/AAAA** e a hora como **HH:MM**.", ephemeral=True)
        return

    db.create_record(
        kind="aula",
        titulo=titulo,
        descricao=descricao or "",
        starts_at=dt,
        voice_channel_id=canal_voz.id,
        role_id=cargo.id,
        participant_ids=[],
        created_by=interaction.user.id,
        notify_before=ANTECEDENCIA_PADRAO,
    )

    embed = discord.Embed(
        title="📚 Aula registrada na Agenda Mágica",
        description=f"{BOT_PERSONA} anotou a aula e enviará uma coruja aos membros do cargo escolhido.",
        color=discord.Color.green(),
    )
    embed.add_field(name="Aula", value=titulo, inline=False)
    embed.add_field(name="Cargo", value=cargo.mention, inline=True)
    embed.add_field(name="Quando", value=dt.astimezone(FUSO_BRASILIA).strftime("%d/%m/%Y às %H:%M"), inline=True)
    embed.add_field(name="Canal de voz", value=canal_voz.mention, inline=False)
    if descricao:
        embed.add_field(name="Detalhes", value=descricao, inline=False)
    embed.set_footer(text=f"{BOT_PERSONA} está feliz em ajudar!")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="reuniao_criar", description="Agendar reunião entre pessoas específicas")
@app_commands.describe(
    titulo="Assunto da reunião",
    participante_1="Primeira pessoa",
    participante_2="Segunda pessoa",
    data="Data no formato DD/MM/AAAA",
    hora="Hora no formato HH:MM",
    canal_voz="Canal de voz da reunião",
    descricao="Detalhes opcionais",
)
async def reuniao_criar(
    interaction: discord.Interaction,
    titulo: str,
    participante_1: discord.Member,
    participante_2: discord.Member,
    data: str,
    hora: str,
    canal_voz: discord.VoiceChannel,
    descricao: Optional[str] = None,
):
    if not await staff_check(interaction):
        return
    try:
        dt = parse_datetime(data, hora)
    except ValueError:
        await interaction.response.send_message("Use a data como **DD/MM/AAAA** e a hora como **HH:MM**.", ephemeral=True)
        return

    db.create_record(
        kind="reuniao",
        titulo=titulo,
        descricao=descricao or "",
        starts_at=dt,
        voice_channel_id=canal_voz.id,
        role_id=None,
        participant_ids=[participante_1.id, participante_2.id],
        created_by=interaction.user.id,
        notify_before=ANTECEDENCIA_PADRAO,
    )

    embed = discord.Embed(
        title="🗓️ Reunião registrada na Agenda Mágica",
        description=f"{BOT_PERSONA} registrou a reunião e avisará os participantes por coruja.",
        color=discord.Color.purple(),
    )
    embed.add_field(name="Assunto", value=titulo, inline=False)
    embed.add_field(name="Participantes", value=f"{participante_1.mention} e {participante_2.mention}", inline=False)
    embed.add_field(name="Quando", value=dt.astimezone(FUSO_BRASILIA).strftime("%d/%m/%Y às %H:%M"), inline=True)
    embed.add_field(name="Canal de voz", value=canal_voz.mention, inline=True)
    if descricao:
        embed.add_field(name="Detalhes", value=descricao, inline=False)
    embed.set_footer(text=f"{BOT_PERSONA} está feliz em ajudar!")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="registro_editar", description="Editar um registro existente")
@app_commands.describe(
    registro="Selecione o registro",
    titulo="Novo título",
    data="Nova data DD/MM/AAAA",
    hora="Nova hora HH:MM",
    canal_voz="Novo canal de voz",
    descricao="Nova descrição",
)
@app_commands.autocomplete(registro=record_autocomplete)
async def registro_editar(
    interaction: discord.Interaction,
    registro: str,
    titulo: Optional[str] = None,
    data: Optional[str] = None,
    hora: Optional[str] = None,
    canal_voz: Optional[discord.VoiceChannel] = None,
    descricao: Optional[str] = None,
):
    if not await staff_check(interaction):
        return
    row = db.get_record(int(registro))
    if not row or row["canceled"]:
        await interaction.response.send_message("{BOT_PERSONA} não encontrou esse registro.", ephemeral=True)
        return

    starts_at = None
    if data and hora:
        try:
            starts_at = parse_datetime(data, hora)
        except ValueError:
            await interaction.response.send_message("Use a data como **DD/MM/AAAA** e a hora como **HH:MM**.", ephemeral=True)
            return
    elif data or hora:
        await interaction.response.send_message("Para alterar data e hora, informe os dois campos juntos.", ephemeral=True)
        return

    db.update_record(
        int(registro),
        titulo=titulo,
        descricao=descricao,
        starts_at=starts_at,
        voice_channel_id=canal_voz.id if canal_voz else None,
    )

    updated = db.get_record(int(registro))
    embed = discord.Embed(
        title="✏️ Registro atualizado",
        description=f"{BOT_PERSONA} reorganizou este compromisso da {SYSTEM_NAME}.",
        color=discord.Color.orange(),
    )
    embed.add_field(name="Registro", value=updated["titulo"], inline=False)
    embed.add_field(name="Quando", value=format_dt(updated["starts_at"]), inline=True)
    embed.add_field(name="Canal de voz", value=voice_mention(interaction.guild, updated["voice_channel_id"]), inline=True)
    if updated["descricao"]:
        embed.add_field(name="Detalhes", value=updated["descricao"], inline=False)
    embed.set_footer(text=f"{BOT_PERSONA} está feliz em ajudar!")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="registro_cancelar", description="Cancelar um registro existente")
@app_commands.describe(registro="Selecione o registro")
@app_commands.autocomplete(registro=record_autocomplete)
async def registro_cancelar(interaction: discord.Interaction, registro: str):
    if not await staff_check(interaction):
        return
    row = db.get_record(int(registro))
    if not row or row["canceled"]:
        await interaction.response.send_message(f"{BOT_PERSONA} não encontrou esse registro.", ephemeral=True)
        return
    db.cancel_record(int(registro))
    await interaction.response.send_message(
        f"🧹 {BOT_PERSONA} cancelou **{row['titulo']}** e removeu o compromisso da {SYSTEM_NAME}.",
        ephemeral=True,
    )


@bot.tree.command(name="agenda_staff", description="Ver próximos registros da staff")
async def agenda_staff(interaction: discord.Interaction):
    if not await staff_check(interaction):
        return
    rows = db.upcoming_staff()
    if not rows:
        await interaction.response.send_message(f"🧹 {BOT_PERSONA} não encontrou registros futuros na {SYSTEM_NAME}.", ephemeral=True)
        return
    embed = discord.Embed(title="📜 Próximos registros da Agenda Mágica", color=discord.Color.blurple())
    for row in rows[:10]:
        embed.add_field(
            name=record_label(row),
            value=f"Tipo: **{row['kind'].title()}**\nCanal: {voice_mention(interaction.guild, row['voice_channel_id'])}",
            inline=False,
        )
    embed.set_footer(text=f"{BOT_PERSONA} organizou os próximos registros.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="minha_agenda", description="Ver seus próximos compromissos")
async def minha_agenda(interaction: discord.Interaction):
    rows = db.upcoming_for_user(interaction.user.id)
    if not rows:
        await interaction.response.send_message(f"🧹 {BOT_PERSONA} não encontrou compromissos futuros para você.", ephemeral=True)
        return
    embed = discord.Embed(title="🗓️ Sua Agenda Mágica", color=discord.Color.blurple())
    for row in rows[:10]:
        embed.add_field(
            name=row["titulo"],
            value=f"Quando: **{format_dt(row['starts_at'])}**\nCanal: {voice_mention(interaction.guild, row['voice_channel_id'])}",
            inline=False,
        )
    embed.set_footer(text=f"{BOT_PERSONA} separou seus próximos compromissos.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="agenda_hoje", description="Ver seus compromissos de hoje")
async def agenda_hoje(interaction: discord.Interaction):
    rows = db.today_for_user(interaction.user.id)
    if not rows:
        await interaction.response.send_message(f"🧹 {BOT_PERSONA} não encontrou compromissos para hoje.", ephemeral=True)
        return
    embed = discord.Embed(title="🌙 Seus compromissos de hoje", color=discord.Color.teal())
    for row in rows:
        embed.add_field(
            name=row["titulo"],
            value=f"Quando: **{format_dt(row['starts_at'])}**\nCanal: {voice_mention(interaction.guild, row['voice_channel_id'])}",
            inline=False,
        )
    embed.set_footer(text=f"{BOT_PERSONA} organizou seus horários de hoje.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# =========================
# Loops
# =========================
@tasks.loop(minutes=1)
async def reminder_loop():
    db.delete_old()
    rows = db.pending_notifications()
    for row in rows:
        guild = bot.get_guild(GUILD_ID) if GUILD_ID else None
        if not guild:
            continue
        starts_at = from_db_dt(row["starts_at"])
        now = now_brasilia()
        before_dt = starts_at - timedelta(minutes=int(row["notify_before"] or ANTECEDENCIA_PADRAO))

        if not row["notify_sent"] and now >= before_dt and now < starts_at + timedelta(minutes=1):
            await notify_record(row, guild, started=False)
            with db._conn() as conn:
                conn.execute("UPDATE registros SET notify_sent = 1 WHERE id = ?", (row["id"],))

        if not row["start_sent"] and now >= starts_at:
            await notify_record(row, guild, started=True)
            with db._conn() as conn:
                conn.execute("UPDATE registros SET start_sent = 1 WHERE id = ?", (row["id"],))


@reminder_loop.before_loop
async def before_reminder_loop():
    await bot.wait_until_ready()


# =========================
# Eventos
# =========================
@bot.event
async def on_ready():
    try:
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(f"Comandos sincronizados no servidor: {len(synced)}")
        else:
            synced = await bot.tree.sync()
            print(f"Comandos globais sincronizados: {len(synced)}")
    except Exception as exc:
        print(f"Erro ao sincronizar comandos: {exc}")

    if not reminder_loop.is_running():
        reminder_loop.start()

    print(f"Bot conectado como {bot.user}")


if __name__ == "__main__":
    bot.run(TOKEN)
