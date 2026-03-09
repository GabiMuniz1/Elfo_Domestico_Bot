import os
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv

from zoneinfo import ZoneInfo
from datetime import datetime

# =========================
# Configuração
# =========================
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Defina a variável de ambiente DISCORD_TOKEN antes de iniciar o bot.")

DB_PATH = os.getenv("AGENDA_DB_PATH", "agenda_hogwarts.db")
ANTECEDENCIA_PADRAO = int(os.getenv("ANTECEDENCIA_PADRAO", "10"))
STAFF_ROLES = {
    item.strip()
    for item in os.getenv(
        "STAFF_ROLES",
        "Diretor,Vice-Diretor,Professores,Monitores",
    ).split(",")
    if item.strip()
}
BOT_PERSONA = "Mispy"
SYSTEM_NAME = "Agenda Mágica"
DATE_FMT_DB = "%Y-%m-%d %H:%M:%S"

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

FUSO_BRASIL = ZoneInfo("America/Sao_Paulo")
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
                    guild_id INTEGER NOT NULL,
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
            cols = [r[1] for r in conn.execute("PRAGMA table_info(registros)").fetchall()]
            if "guild_id" not in cols:
                conn.execute("ALTER TABLE registros ADD COLUMN guild_id INTEGER DEFAULT 0")

    def create_record(
        self,
        *,
        guild_id: int,
        kind: str,
        titulo: str,
        descricao: str,
        starts_at: datetime,
        voice_channel_id: Optional[int],
        role_id: Optional[int],
        participant_ids: list[int],
        created_by: int,
        notify_before: int,
    ) -> int:
        now = datetime.now().strftime(DATE_FMT_DB)
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO registros (
                    guild_id, kind, titulo, descricao, starts_at,
                    voice_channel_id, role_id, participant_ids, created_by,
                    notify_before, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    kind,
                    titulo,
                    descricao,
                    starts_at.strftime(DATE_FMT_DB),
                    voice_channel_id,
                    role_id,
                    ",".join(map(str, participant_ids)),
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
        guild_id: int,
        *,
        titulo: Optional[str] = None,
        descricao: Optional[str] = None,
        starts_at: Optional[datetime] = None,
        voice_channel_id: Optional[int] = None,
        notify_before: Optional[int] = None,
    ) -> bool:
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
            values.append(starts_at.strftime(DATE_FMT_DB))
            fields.append("notify_sent = 0")
            fields.append("start_sent = 0")
        if voice_channel_id is not None:
            fields.append("voice_channel_id = ?")
            values.append(voice_channel_id)
        if notify_before is not None:
            fields.append("notify_before = ?")
            values.append(notify_before)
            fields.append("notify_sent = 0")
        if not fields:
            return False
        fields.append("updated_at = ?")
        values.append(datetime.now().strftime(DATE_FMT_DB))
        values.extend([record_id, guild_id])
        with self._conn() as conn:
            conn.execute(
                f"UPDATE registros SET {', '.join(fields)} WHERE id = ? AND guild_id = ?",
                values,
            )
        return True

    def cancel_record(self, record_id: int, guild_id: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE registros SET canceled = 1, updated_at = ? WHERE id = ? AND guild_id = ?",
                (datetime.now().strftime(DATE_FMT_DB), record_id, guild_id),
            )

    def record_by_id(self, record_id: int, guild_id: int):
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT * FROM registros WHERE id = ? AND guild_id = ?",
                (record_id, guild_id),
            )
            return cur.fetchone()

    def autocomplete_records(self, guild_id: int):
        cutoff = (datetime.now() - timedelta(days=1)).strftime(DATE_FMT_DB)
        with self._conn() as conn:
            cur = conn.execute(
                """
                SELECT * FROM registros
                WHERE guild_id = ? AND canceled = 0 AND starts_at >= ?
                ORDER BY starts_at ASC
                LIMIT 25
                """,
                (guild_id, cutoff),
            )
            return cur.fetchall()

    def upcoming_for_user(self, guild_id: int, user_id: int):
        now = datetime.now().strftime(DATE_FMT_DB)
        pattern_a = f"{user_id},%"
        pattern_b = f"%,{user_id},%"
        pattern_c = f"%,{user_id}"
        with self._conn() as conn:
            cur = conn.execute(
                """
                SELECT * FROM registros
                WHERE guild_id = ?
                  AND canceled = 0
                  AND starts_at >= ?
                  AND (
                    participant_ids = ? OR participant_ids LIKE ? OR participant_ids LIKE ? OR participant_ids LIKE ?
                  )
                ORDER BY starts_at ASC
                LIMIT 25
                """,
                (guild_id, now, str(user_id), pattern_a, pattern_b, pattern_c),
            )
            return cur.fetchall()

    def today_for_user(self, guild_id: int, user_id: int):
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        pattern_a = f"{user_id},%"
        pattern_b = f"%,{user_id},%"
        pattern_c = f"%,{user_id}"
        with self._conn() as conn:
            cur = conn.execute(
                """
                SELECT * FROM registros
                WHERE guild_id = ?
                  AND canceled = 0
                  AND starts_at >= ?
                  AND starts_at < ?
                  AND (
                    participant_ids = ? OR participant_ids LIKE ? OR participant_ids LIKE ? OR participant_ids LIKE ?
                  )
                ORDER BY starts_at ASC
                """,
                (
                    guild_id,
                    start.strftime(DATE_FMT_DB),
                    end.strftime(DATE_FMT_DB),
                    str(user_id),
                    pattern_a,
                    pattern_b,
                    pattern_c,
                ),
            )
            return cur.fetchall()

    def upcoming_staff(self, guild_id: int):
        now = datetime.now().strftime(DATE_FMT_DB)
        with self._conn() as conn:
            cur = conn.execute(
                """
                SELECT * FROM registros
                WHERE guild_id = ? AND canceled = 0 AND starts_at >= ?
                ORDER BY starts_at ASC
                LIMIT 25
                """,
                (guild_id, now),
            )
            return cur.fetchall()

    def pending_notifications(self):
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT * FROM registros WHERE canceled = 0 AND (notify_sent = 0 OR start_sent = 0)"
            )
            return cur.fetchall()

    def mark_notify_sent(self, record_id: int):
        with self._conn() as conn:
            conn.execute("UPDATE registros SET notify_sent = 1 WHERE id = ?", (record_id,))

    def mark_start_sent(self, record_id: int):
        with self._conn() as conn:
            conn.execute("UPDATE registros SET start_sent = 1 WHERE id = ?", (record_id,))

    def cleanup_old(self):
        cutoff = (datetime.now() - timedelta(hours=12)).strftime(DATE_FMT_DB)
        with self._conn() as conn:
            conn.execute("DELETE FROM registros WHERE starts_at < ?", (cutoff,))


db = Database(DB_PATH)


# =========================
# Utilitários
# =========================
def ensure_guild(interaction: discord.Interaction) -> Optional[discord.Guild]:
    return interaction.guild


def is_staff(member: discord.Member) -> bool:
    return any(role.name in STAFF_ROLES for role in member.roles)


async def deny_staff(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"🧹 {BOT_PERSONA} pede desculpas, mas apenas **Diretor, Vice-Diretor, Professores e Monitores** podem usar os comandos da moderação.",
        ephemeral=True,
    )


def parse_datetime(data: str, horario: str) -> datetime:
    return datetime.strptime(f"{data} {horario}", "%d/%m/%Y %H:%M")


def fmt_db(dt: datetime) -> str:
    return dt.strftime(DATE_FMT_DB)


def fmt_human(dt_str: str) -> str:
    return datetime.strptime(dt_str, DATE_FMT_DB).strftime("%d/%m/%Y às %H:%M")


def record_title(row: sqlite3.Row) -> str:
    return f"{row['titulo']} • {datetime.strptime(row['starts_at'], DATE_FMT_DB).strftime('%d/%m %H:%M')}"


def voice_channel_text(guild: discord.Guild, channel_id: Optional[int]) -> str:
    if not channel_id:
        return "Não informado"
    channel = guild.get_channel(channel_id)
    return channel.mention if channel else "Canal não encontrado"


def participant_mentions(guild: discord.Guild, row: sqlite3.Row) -> str:
    ids = [int(x) for x in (row["participant_ids"] or "").split(",") if x.strip()]
    mentions = []
    for uid in ids:
        member = guild.get_member(uid)
        mentions.append(member.mention if member else f"<@{uid}>")
    return ", ".join(mentions) if mentions else "Não informado"


def build_record_embed(guild: discord.Guild, row: sqlite3.Row) -> discord.Embed:
    titles = {
        "aula": "📚 Aula registrada na Agenda Mágica",
        "reuniao": "🪄 Reunião registrada na Agenda Mágica",
        "individual": "📌 Aviso registrado na Agenda Mágica",
    }
    embed = discord.Embed(
        title=titles.get(row["kind"], "📜 Registro na Agenda Mágica"),
        description=f"{BOT_PERSONA} organizou tudo direitinho para você.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Título", value=row["titulo"], inline=False)
    embed.add_field(name="Quando", value=fmt_human(row["starts_at"]), inline=True)
    embed.add_field(name="Aviso antecipado", value=f"{row['notify_before']} min", inline=True)
    embed.add_field(name="Canal de voz", value=voice_channel_text(guild, row["voice_channel_id"]), inline=False)
    if row["kind"] == "aula" and row["role_id"]:
        role = guild.get_role(row["role_id"])
        embed.add_field(name="Público", value=role.mention if role else "Cargo não encontrado", inline=False)
    elif row["kind"] in {"reuniao", "individual"}:
        embed.add_field(name="Participantes", value=participant_mentions(guild, row), inline=False)
    if row["descricao"]:
        embed.add_field(name="Observações", value=row["descricao"], inline=False)
    embed.set_footer(text=f"{BOT_PERSONA} está feliz em ajudar com a {SYSTEM_NAME}.")
    return embed


async def send_dm_safe(user: discord.abc.User, embed: discord.Embed):
    try:
        await user.send(embed=embed)
        return True
    except Exception:
        return False


async def notify_user_before(user: discord.abc.User, titulo: str, quando: str, minutos: int, canal: str):
    embed = discord.Embed(
        title="🦉 Sua coruja trouxe um lembrete",
        description=f"{BOT_PERSONA} avisa que **{titulo}** começará em **{minutos} minutos**.",
        color=discord.Color.gold(),
    )
    embed.add_field(name="Quando", value=quando, inline=True)
    embed.add_field(name="Canal de voz", value=canal, inline=False)
    embed.set_footer(text=f"{SYSTEM_NAME} • Hogwarts")
    await send_dm_safe(user, embed)


async def notify_user_start(user: discord.abc.User, titulo: str, quando: str, canal: str):
    embed = discord.Embed(
        title="🦉 Sua coruja voltou com outro aviso",
        description=f"{BOT_PERSONA} avisa que **{titulo}** começa agora.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Horário", value=quando, inline=True)
    embed.add_field(name="Canal de voz", value=canal, inline=False)
    embed.set_footer(text=f"{SYSTEM_NAME} • Hogwarts")
    await send_dm_safe(user, embed)


async def dispatch_notifications(row: sqlite3.Row, before: bool):
    guild = bot.get_guild(row["guild_id"])
    if guild is None:
        return

    canal_texto = voice_channel_text(guild, row["voice_channel_id"])
    quando = fmt_human(row["starts_at"])
    ids: list[int] = []

    if row["kind"] == "aula" and row["role_id"]:
        role = guild.get_role(row["role_id"])
        if role:
            ids = [member.id for member in role.members if not member.bot]
    else:
        ids = [int(x) for x in (row["participant_ids"] or "").split(",") if x.strip()]

    for uid in ids:
        member = guild.get_member(uid)
        if member is None:
            try:
                member = await bot.fetch_user(uid)
            except Exception:
                continue
        try:
            if before:
                await notify_user_before(member, row["titulo"], quando, row["notify_before"], canal_texto)
            else:
                await notify_user_start(member, row["titulo"], quando, canal_texto)
        except Exception:
            continue


async def sync_commands_for_all_guilds():
    total = 0
    for guild in bot.guilds:
        try:
            discord_guild = discord.Object(id=guild.id)
            bot.tree.copy_global_to(guild=discord_guild)
            synced = await bot.tree.sync(guild=discord_guild)
            total += len(synced)
            print(f"Comandos sincronizados em {guild.name}: {len(synced)}")
        except Exception as exc:
            print(f"Erro ao sincronizar em {guild.name}: {exc}")
    return total


# =========================
# Autocomplete
# =========================
async def registro_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    if interaction.guild is None:
        return []
    registros = db.autocomplete_records(interaction.guild.id)
    opcoes = []
    current_lower = current.lower().strip()
    for row in registros:
        label = record_title(row)
        if current_lower and current_lower not in label.lower() and current_lower not in row["titulo"].lower():
            continue
        opcoes.append(app_commands.Choice(name=label[:100], value=str(row["id"])))
    return opcoes[:25]


# =========================
# Eventos do bot
# =========================
@bot.event
async def on_ready():
    total = await sync_commands_for_all_guilds()
    if not reminder_loop.is_running():
        reminder_loop.start()
    print(f"Bot conectado como {bot.user}")
    print(f"Total de registros de comandos sincronizados: {total}")


@bot.event
async def on_guild_join(guild: discord.Guild):
    try:
        discord_guild = discord.Object(id=guild.id)
        bot.tree.copy_global_to(guild=discord_guild)
        synced = await bot.tree.sync(guild=discord_guild)
        print(f"Sincronizado em novo servidor {guild.name}: {len(synced)}")
    except Exception as exc:
        print(f"Erro ao sincronizar novo servidor {guild.name}: {exc}")


# =========================
# Loop de lembretes
# =========================
@tasks.loop(seconds=30)
async def reminder_loop():
    now = datetime.now()
    for row in db.pending_notifications():
        starts_at = datetime.strptime(row["starts_at"], DATE_FMT_DB)
        notify_at = starts_at - timedelta(minutes=row["notify_before"])

        if row["notify_sent"] == 0 and now >= notify_at and now < starts_at:
            await dispatch_notifications(row, before=True)
            db.mark_notify_sent(row["id"])

        if row["start_sent"] == 0 and now >= starts_at:
            await dispatch_notifications(row, before=False)
            db.mark_start_sent(row["id"])

    db.cleanup_old()


# =========================
# Comandos
# =========================
@bot.tree.command(name="ajuda_agenda", description="Mispy explica como usar a Agenda Mágica")
async def ajuda_agenda(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🧹 Mispy explica a Agenda Mágica",
        description=(
            "Mispy está feliz em ajudar com aulas, reuniões e avisos do castelo.\n\n"
            "**Comandos da moderação**\n"
            "`/aula_criar` cria aula para um cargo.\n"
            "`/reuniao_criar` agenda encontro entre pessoas.\n"
            "`/aviso_individual` cria lembrete privado.\n"
            "`/registro_editar` altera horário, canal ou antecedência.\n"
            "`/registro_cancelar` cancela um registro.\n"
            "`/agenda_staff` mostra os próximos registros do servidor.\n\n"
            "**Comandos dos membros**\n"
            "`/minha_agenda` mostra seus próximos compromissos.\n"
            "`/agenda_hoje` mostra o que você tem hoje.\n\n"
            "**Coruja da Agenda**\n"
            "Os avisos chegam na DM antes do evento e novamente quando ele começa."
        ),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"{BOT_PERSONA} está feliz em ajudar!")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="aula_criar", description="Criar uma aula para um cargo")
@app_commands.describe(
    titulo="Nome da aula",
    cargo="Cargo que receberá o lembrete",
    data="Data no formato DD/MM/AAAA",
    horario="Horário no formato HH:MM",
    canal_voz="Canal de voz da aula",
    antecedencia="Minutos antes do começo para avisar",
    descricao="Observações opcionais",
)
async def aula_criar(
    interaction: discord.Interaction,
    titulo: str,
    cargo: discord.Role,
    data: str,
    horario: str,
    canal_voz: discord.VoiceChannel,
    antecedencia: app_commands.Range[int, 0, 1440] = ANTECEDENCIA_PADRAO,
    descricao: Optional[str] = None,
):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
        await deny_staff(interaction)
        return
    try:
        starts_at = parse_datetime(data, horario)
    except ValueError:
        await interaction.response.send_message("Use a data como **DD/MM/AAAA** e o horário como **HH:MM**.", ephemeral=True)
        return

    record_id = db.create_record(
        guild_id=interaction.guild.id,
        kind="aula",
        titulo=titulo,
        descricao=descricao or "",
        starts_at=starts_at,
        voice_channel_id=canal_voz.id,
        role_id=cargo.id,
        participant_ids=[],
        created_by=interaction.user.id,
        notify_before=antecedencia,
    )
    row = db.record_by_id(record_id, interaction.guild.id)
    embed = build_record_embed(interaction.guild, row)
    embed.description = f"{BOT_PERSONA} anotou a aula na **{SYSTEM_NAME}** e vai avisar quem tiver o cargo {cargo.mention}."
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="reuniao_criar", description="Criar uma reunião entre duas pessoas")
@app_commands.describe(
    titulo="Assunto da reunião",
    participante1="Primeiro participante",
    participante2="Segundo participante",
    data="Data no formato DD/MM/AAAA",
    horario="Horário no formato HH:MM",
    canal_voz="Canal de voz da reunião",
    antecedencia="Minutos antes do começo para avisar",
    descricao="Observações opcionais",
)
async def reuniao_criar(
    interaction: discord.Interaction,
    titulo: str,
    participante1: discord.Member,
    participante2: discord.Member,
    data: str,
    horario: str,
    canal_voz: discord.VoiceChannel,
    antecedencia: app_commands.Range[int, 0, 1440] = ANTECEDENCIA_PADRAO,
    descricao: Optional[str] = None,
):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
        await deny_staff(interaction)
        return
    try:
        starts_at = parse_datetime(data, horario)
    except ValueError:
        await interaction.response.send_message("Use a data como **DD/MM/AAAA** e o horário como **HH:MM**.", ephemeral=True)
        return

    participants = list(dict.fromkeys([participante1.id, participante2.id]))
    record_id = db.create_record(
        guild_id=interaction.guild.id,
        kind="reuniao",
        titulo=titulo,
        descricao=descricao or "",
        starts_at=starts_at,
        voice_channel_id=canal_voz.id,
        role_id=None,
        participant_ids=participants,
        created_by=interaction.user.id,
        notify_before=antecedencia,
    )
    row = db.record_by_id(record_id, interaction.guild.id)
    embed = build_record_embed(interaction.guild, row)
    embed.description = f"{BOT_PERSONA} registrou a reunião e mandará corujas para os participantes na hora certa."
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="aviso_individual", description="Criar um lembrete privado para uma pessoa")
@app_commands.describe(
    titulo="Título do lembrete",
    pessoa="Pessoa que vai receber a coruja",
    data="Data no formato DD/MM/AAAA",
    horario="Horário no formato HH:MM",
    antecedencia="Minutos antes do começo para avisar",
    canal_voz="Canal de voz opcional",
    descricao="Observações opcionais",
)
async def aviso_individual(
    interaction: discord.Interaction,
    titulo: str,
    pessoa: discord.Member,
    data: str,
    horario: str,
    antecedencia: app_commands.Range[int, 0, 1440] = ANTECEDENCIA_PADRAO,
    canal_voz: Optional[discord.VoiceChannel] = None,
    descricao: Optional[str] = None,
):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
        await deny_staff(interaction)
        return
    try:
        starts_at = parse_datetime(data, horario)
    except ValueError:
        await interaction.response.send_message("Use a data como **DD/MM/AAAA** e o horário como **HH:MM**.", ephemeral=True)
        return

    record_id = db.create_record(
        guild_id=interaction.guild.id,
        kind="individual",
        titulo=titulo,
        descricao=descricao or "",
        starts_at=starts_at,
        voice_channel_id=canal_voz.id if canal_voz else None,
        role_id=None,
        participant_ids=[pessoa.id],
        created_by=interaction.user.id,
        notify_before=antecedencia,
    )
    row = db.record_by_id(record_id, interaction.guild.id)
    embed = build_record_embed(interaction.guild, row)
    embed.description = f"{BOT_PERSONA} já separou uma coruja para lembrar {pessoa.mention}."
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="agenda_staff", description="Ver os próximos registros do servidor")
async def agenda_staff(interaction: discord.Interaction):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
        await deny_staff(interaction)
        return
    rows = db.upcoming_staff(interaction.guild.id)
    if not rows:
        await interaction.response.send_message(f"🧹 {BOT_PERSONA} não encontrou registros futuros neste servidor.", ephemeral=True)
        return
    embed = discord.Embed(
        title=f"📚 {SYSTEM_NAME} do servidor",
        description=f"{BOT_PERSONA} separou os próximos compromissos deste castelo.",
        color=discord.Color.dark_teal(),
    )
    for row in rows[:10]:
        tipo = {"aula": "Aula", "reuniao": "Reunião", "individual": "Aviso"}.get(row["kind"], "Registro")
        embed.add_field(
            name=f"{tipo} • {row['titulo']}",
            value=f"{fmt_human(row['starts_at'])}\nCanal: {voice_channel_text(interaction.guild, row['voice_channel_id'])}",
            inline=False,
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="minha_agenda", description="Ver seus próximos lembretes")
async def minha_agenda(interaction: discord.Interaction):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Este comando funciona dentro de um servidor.", ephemeral=True)
        return
    rows = db.upcoming_for_user(interaction.guild.id, interaction.user.id)
    if not rows:
        await interaction.response.send_message(f"🧹 {BOT_PERSONA} não encontrou compromissos futuros para você neste servidor.", ephemeral=True)
        return
    embed = discord.Embed(
        title="📜 Sua Agenda Mágica",
        description=f"{BOT_PERSONA} separou seus próximos compromissos.",
        color=discord.Color.purple(),
    )
    for row in rows[:10]:
        embed.add_field(
            name=row["titulo"],
            value=f"{fmt_human(row['starts_at'])}\nCanal: {voice_channel_text(interaction.guild, row['voice_channel_id'])}",
            inline=False,
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="agenda_hoje", description="Ver seus lembretes de hoje")
async def agenda_hoje(interaction: discord.Interaction):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Este comando funciona dentro de um servidor.", ephemeral=True)
        return
    rows = db.today_for_user(interaction.guild.id, interaction.user.id)
    if not rows:
        await interaction.response.send_message(f"🧹 {BOT_PERSONA} não encontrou compromissos para hoje neste servidor.", ephemeral=True)
        return
    embed = discord.Embed(
        title="🗓️ Seus compromissos de hoje",
        description=f"{BOT_PERSONA} já colocou tudo em ordem para hoje.",
        color=discord.Color.green(),
    )
    for row in rows[:10]:
        embed.add_field(
            name=row["titulo"],
            value=f"{fmt_human(row['starts_at'])}\nCanal: {voice_channel_text(interaction.guild, row['voice_channel_id'])}",
            inline=False,
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="registro_editar", description="Editar um registro da Agenda Mágica")
@app_commands.autocomplete(registro=registro_autocomplete)
@app_commands.describe(
    registro="Escolha o registro",
    titulo="Novo título",
    data="Nova data DD/MM/AAAA",
    horario="Novo horário HH:MM",
    canal_voz="Novo canal de voz",
    antecedencia="Nova antecedência em minutos",
    descricao="Novas observações",
)
async def registro_editar(
    interaction: discord.Interaction,
    registro: str,
    titulo: Optional[str] = None,
    data: Optional[str] = None,
    horario: Optional[str] = None,
    canal_voz: Optional[discord.VoiceChannel] = None,
    antecedencia: Optional[app_commands.Range[int, 0, 1440]] = None,
    descricao: Optional[str] = None,
):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
        await deny_staff(interaction)
        return
    row = db.record_by_id(int(registro), interaction.guild.id)
    if row is None:
        await interaction.response.send_message("🧹 Mispy não encontrou esse registro neste servidor.", ephemeral=True)
        return

    starts_at = None
    if data and horario:
        try:
            starts_at = parse_datetime(data, horario)
        except ValueError:
            await interaction.response.send_message("Use a data como **DD/MM/AAAA** e o horário como **HH:MM**.", ephemeral=True)
            return
    elif data or horario:
        await interaction.response.send_message("Para alterar a data, envie **data e horário juntos**.", ephemeral=True)
        return

    db.update_record(
        int(registro),
        interaction.guild.id,
        titulo=titulo,
        descricao=descricao,
        starts_at=starts_at,
        voice_channel_id=canal_voz.id if canal_voz else None,
        notify_before=antecedencia,
    )
    updated = db.record_by_id(int(registro), interaction.guild.id)
    embed = build_record_embed(interaction.guild, updated)
    embed.description = f"{BOT_PERSONA} atualizou o registro sem bagunçar os pergaminhos da {SYSTEM_NAME}."
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="registro_cancelar", description="Cancelar um registro da Agenda Mágica")
@app_commands.autocomplete(registro=registro_autocomplete)
async def registro_cancelar(interaction: discord.Interaction, registro: str):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
        await deny_staff(interaction)
        return
    row = db.record_by_id(int(registro), interaction.guild.id)
    if row is None:
        await interaction.response.send_message("🧹 Mispy não encontrou esse registro neste servidor.", ephemeral=True)
        return
    db.cancel_record(int(registro), interaction.guild.id)
    await interaction.response.send_message(
        f"🧹 {BOT_PERSONA} cancelou **{row['titulo']}** e guardou esse pergaminho para não poluir a agenda.",
        ephemeral=True,
    )

bot.run(TOKEN)
