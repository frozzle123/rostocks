# main.py - RoStock Discord Shop Bot
import os
import json
import sqlite3
import uuid
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
import discord
from discord import app_commands
from discord.ext import commands

# Load environment variables
load_dotenv()

# ============ REQUIRED ENV VARIABLES ============
TOKEN = os.getenv('BOT_TOKEN')
CLIENT_ID = os.getenv('CLIENT_ID')
GUILD_ID = os.getenv('GUILD_ID')

# Optional env variables with defaults
CURRENCY_SYMBOL = os.getenv('CURRENCY_SYMBOL', '💎')

# Check required variables
if not TOKEN:
    print("❌ Error: BOT_TOKEN is required in .env file!")
    exit(1)
if not CLIENT_ID:
    print("❌ Error: CLIENT_ID is required in .env file!")
    exit(1)
if not GUILD_ID:
    print("❌ Error: GUILD_ID is required in .env file!")
    exit(1)

# ============ CONFIGURATION ============
ROSTOCK_COLORS = {
    'primary': 0x5865F2,
    'success': 0x57F287,
    'error': 0xED4245,
    'gold': 0xFEE75C,
    'purple': 0xEB459E,
    'dark': 0x2B2D31,
}

ROSTOCK_LOGO = "https://cdn.discordapp.com/icons/1532454881284063295/15bf1fd971ad4cea73c9acd47667bfc9.webp?size=2048"
ROSTOCK_BANNER = "https://cdn.discordapp.com/attachments/1544086303975669890/1544097548166238208/EAFC7DD0-FE41-4C18-8F8C-A07FE79CB16D.png?ex=6a974467&is=6a95f2e7&hm=699fd8734b4542e304d2c9780974de69803a16db48c68c825cf0794291192a78&"

CONFIG = {
    'currency_symbol': CURRENCY_SYMBOL,
    'order_prefix': 'RO-',
    'payment_timeout': 30,
    'categories': ['STREAMING', 'DISCORD', 'GAMING', 'DIGITAL', 'ACCOUNTS', 'BOTS'],
    'ticket_category_id': 0,
    'support_role_id': 0,
    'admin_role_id': 0,
    'ticket_log_channel_id': 0,
    'colors': {
        'primary': ROSTOCK_COLORS['primary'],
        'success': ROSTOCK_COLORS['success'],
        'error': ROSTOCK_COLORS['error'],
        'warning': ROSTOCK_COLORS['gold'],
        'info': ROSTOCK_COLORS['purple'],
    }
}

# ============ DATABASE SETUP ============
# Use /app/shop.db for Railway, or local shop.db for development
DB_PATH = os.getenv('DATABASE_PATH', '/app/shop.db')

# Ensure the directory exists
db_dir = os.path.dirname(DB_PATH)
if db_dir and not os.path.exists(db_dir):
    try:
        os.makedirs(db_dir, exist_ok=True)
        print(f"✅ Created directory: {db_dir}")
    except Exception as e:
        print(f"❌ Could not create directory: {e}")

# Connect to database
try:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    cursor = conn.cursor()
    print(f"✅ Database connected: {DB_PATH}")
except sqlite3.OperationalError as e:
    print(f"❌ Database error: {e}")
    print(f"📁 Trying to create database at: {DB_PATH}")
    # Try with a different path as fallback
    try:
        DB_PATH = 'shop.db'
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        cursor = conn.cursor()
        print(f"✅ Database connected (fallback): {DB_PATH}")
    except Exception as e2:
        print(f"❌ Failed to connect to database: {e2}")
        raise

cursor.executescript('''
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        description TEXT DEFAULT '',
        price REAL NOT NULL,
        category TEXT NOT NULL,
        stock INTEGER DEFAULT 0,
        delivery_data TEXT DEFAULT '',
        seller_id TEXT DEFAULT '',
        seller_name TEXT DEFAULT '',
        active INTEGER DEFAULT 1,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS orders (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        product_id INTEGER,
        product_name TEXT,
        seller_id TEXT,
        seller_name TEXT,
        price REAL,
        quantity INTEGER DEFAULT 1,
        status TEXT DEFAULT 'pending',
        payment_method TEXT DEFAULT '',
        payment_amount REAL DEFAULT 0,
        payment_txid TEXT DEFAULT '',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        paid_at DATETIME,
        delivered_at DATETIME,
        expires_at DATETIME,
        ticket_channel_id TEXT DEFAULT '',
        ticket_message_id TEXT DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        balance REAL DEFAULT 0,
        total_spent REAL DEFAULT 0,
        orders_count INTEGER DEFAULT 0,
        blacklisted INTEGER DEFAULT 0,
        blacklist_reason TEXT DEFAULT '',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id TEXT UNIQUE NOT NULL,
        order_id TEXT,
        user_id TEXT NOT NULL,
        seller_id TEXT,
        channel_id TEXT NOT NULL,
        status TEXT DEFAULT 'open',
        ticket_type TEXT DEFAULT 'purchase',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        closed_at DATETIME,
        closed_by TEXT
    );

    CREATE TABLE IF NOT EXISTS ticket_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER,
        user_id TEXT NOT NULL,
        message TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id TEXT,
        user_id TEXT,
        product_name TEXT,
        rating INTEGER,
        comment TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS vouchers (
        code TEXT PRIMARY KEY,
        type TEXT,
        value TEXT,
        used_by TEXT,
        used_at DATETIME,
        expires_at DATETIME
    );

    CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT,
        user_id TEXT,
        staff_id TEXT,
        data TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );
''')
conn.commit()
print("✅ Database tables created/verified")

# ============ DISCORD BOT SETUP ============
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

class ShopBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=',', intents=intents)
        self.db = conn
        self.config = CONFIG
        self.synced = False
        self.setup_data = {}

    async def setup_hook(self):
        if not self.synced and GUILD_ID:
            try:
                await self.tree.sync(guild=discord.Object(id=int(GUILD_ID)))
                self.synced = True
                print(f"✅ Synced commands to guild {GUILD_ID}")
            except Exception as e:
                print(f"❌ Failed to sync commands: {e}")

bot = ShopBot()
bot.remove_command('help')

# ============ HELPER FUNCTIONS ============
def ensure_user(user_id):
    cursor.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
    conn.commit()

def log_action(action_type, user_id, staff_id=None, data=None):
    cursor.execute(
        'INSERT INTO logs (type, user_id, staff_id, data) VALUES (?, ?, ?, ?)',
        (action_type, user_id, staff_id, json.dumps(data) if data else None)
    )
    conn.commit()

def create_embed(title, description, color='primary', fields=None, footer=None, image_url=None):
    embed = discord.Embed(
        title=title,
        description=description,
        color=CONFIG['colors'][color] if color in CONFIG['colors'] else CONFIG['colors']['primary']
    )
    if fields:
        for field in fields:
            embed.add_field(name=field['name'], value=field['value'], inline=field.get('inline', True))
    embed.set_footer(text=f"🛒 RoStock • {footer}" if footer else "🛒 RoStock")
    embed.timestamp = datetime.now()
    embed.set_thumbnail(url=ROSTOCK_LOGO)
    if image_url:
        embed.set_image(url=image_url)
    return embed

async def deliver_order(order_id):
    cursor.execute('SELECT * FROM orders WHERE id = ?', (order_id,))
    order = cursor.fetchone()
    if not order or order[6] == 'delivered':
        return False

    cursor.execute('SELECT * FROM products WHERE id = ?', (order[2],))
    product = cursor.fetchone()

    if product and product[5] > 0:
        cursor.execute(
            'UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?',
            (order[4], product[0], order[4])
        )

    cursor.execute('''
        UPDATE orders 
        SET status = 'delivered', 
            delivered_at = CURRENT_TIMESTAMP,
            paid_at = COALESCE(paid_at, CURRENT_TIMESTAMP)
        WHERE id = ?
    ''', (order_id,))
    
    cursor.execute('''
        UPDATE users 
        SET total_spent = total_spent + ?, 
            orders_count = orders_count + 1 
        WHERE user_id = ?
    ''', (order[5] * order[4], order[1]))
    
    conn.commit()
    
    try:
        user = await bot.fetch_user(order[1])
        embed = create_embed(
            '✅ Order Delivered',
            f"**Order ID:** `{order[0]}`\n**Product:** {order[3]}\n\n**Delivery:**\n```\n{product[6] if product else 'Contact support'}\n```",
            'success',
            footer="Thank you for shopping with RoStock!",
            image_url=ROSTOCK_BANNER
        )
        await user.send(embed=embed)
    except:
        pass
    
    return True

def save_setting(key, value):
    cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value))
    conn.commit()

def get_setting(key, default=None):
    cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
    result = cursor.fetchone()
    return result[0] if result else default

# ============ TICKET SYSTEM ============

class TicketView(discord.ui.View):
    def __init__(self, ticket_type='purchase'):
        super().__init__(timeout=None)
        self.ticket_type = ticket_type
    
    @discord.ui.button(label='🎫 Create Ticket', style=discord.ButtonStyle.primary, custom_id='create_ticket', emoji='🎫')
    async def create_ticket_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = PurchaseModal(ticket_type=self.ticket_type)
        await interaction.response.send_modal(modal)

class CloseTicketView(discord.ui.View):
    def __init__(self, ticket_id):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
    
    @discord.ui.button(label='🔒 Close Ticket', style=discord.ButtonStyle.danger, custom_id='close_ticket', emoji='🔒')
    async def close_ticket_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await close_ticket(interaction, self.ticket_id)
    
    @discord.ui.button(label='📋 Transcript', style=discord.ButtonStyle.secondary, custom_id='transcript_ticket', emoji='📋')
    async def transcript_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await send_transcript(interaction, self.ticket_id)

class PurchaseModal(discord.ui.Modal):
    def __init__(self, ticket_type='purchase'):
        super().__init__(title=f'🎫 {ticket_type.capitalize()} Purchase Request')
        self.ticket_type = ticket_type
        
        product_label = get_setting(f'{ticket_type}_product_label', '📦 What product/service do you want?')
        seller_label = get_setting(f'{ticket_type}_seller_label', '👤 Who is the seller?')
        price_label = get_setting(f'{ticket_type}_price_label', '💰 What is the price?')
        details_label = get_setting(f'{ticket_type}_details_label', '📝 Additional details')
        
        self.product = discord.ui.TextInput(
            label=product_label[:45],
            placeholder='e.g., Netflix Account, Discord Nitro, etc.',
            required=True,
            style=discord.TextStyle.paragraph,
            max_length=200
        )
        self.add_item(self.product)
        
        self.seller = discord.ui.TextInput(
            label=seller_label[:45],
            placeholder='e.g., @seller or their Discord ID',
            required=True,
            max_length=100
        )
        self.add_item(self.seller)
        
        self.price = discord.ui.TextInput(
            label=price_label[:45],
            placeholder='e.g., $50 or 50',
            required=True,
            max_length=50
        )
        self.add_item(self.price)
        
        self.details = discord.ui.TextInput(
            label=details_label[:45],
            placeholder='Any specific requirements, delivery method, etc.',
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=500
        )
        self.add_item(self.details)

async def handle_ticket_creation(interaction: discord.Interaction, modal: PurchaseModal):
    try:
        await interaction.response.defer(ephemeral=True)
        
        product = modal.product.value
        seller_input = modal.seller.value
        price = modal.price.value
        details = modal.details.value or 'No additional details provided.'
        ticket_type = modal.ticket_type if hasattr(modal, 'ticket_type') else 'purchase'
        
        seller = None
        seller_name = seller_input
        
        if seller_input.startswith('<@') and seller_input.endswith('>'):
            seller_id = seller_input.replace('<@', '').replace('>', '').replace('!', '')
            try:
                seller = await bot.fetch_user(int(seller_id))
                seller_name = seller.display_name
            except:
                pass
        else:
            try:
                seller = await bot.fetch_user(int(seller_input))
                seller_name = seller.display_name
            except:
                for guild in bot.guilds:
                    for member in guild.members:
                        if seller_input.lower() in member.display_name.lower() or seller_input.lower() in member.name.lower():
                            seller = member
                            seller_name = member.display_name
                            break
                    if seller:
                        break
        
        cursor.execute('SELECT blacklisted FROM users WHERE user_id = ?', (str(interaction.user.id),))
        blacklisted = cursor.fetchone()
        if blacklisted and blacklisted[0] == 1:
            await interaction.followup.send('❌ You are blacklisted from this shop.', ephemeral=True)
            return
        
        ticket_id = f"TICKET-{uuid.uuid4().hex[:8].upper()}"
        
        category_id = int(get_setting('ticket_category_id', 0) or 0)
        category = interaction.guild.get_channel(category_id) if category_id else None
        
        if not category:
            for cat in interaction.guild.categories:
                if cat.name.upper() == 'TICKETS':
                    category = cat
                    break
        
        if not category and interaction.channel.category:
            category = interaction.channel.category
        
        channel_name = f"{ticket_type.lower()}-{interaction.user.display_name[:20]}".replace(' ', '-').lower()
        
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }
        
        if seller:
            overwrites[seller] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        
        support_role_id = int(get_setting('support_role_id', 0) or 0)
        if support_role_id:
            support_role = interaction.guild.get_role(support_role_id)
            if support_role:
                overwrites[support_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        
        admin_role_id = int(get_setting('admin_role_id', 0) or 0)
        if admin_role_id:
            admin_role = interaction.guild.get_role(admin_role_id)
            if admin_role:
                overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        
        channel = await interaction.guild.create_text_channel(
            channel_name,
            category=category,
            overwrites=overwrites,
            topic=f"🎫 {ticket_type.capitalize()} Purchase Ticket - {ticket_id}"
        )
        
        cursor.execute('''
            INSERT INTO tickets (ticket_id, user_id, seller_id, channel_id, status, ticket_type)
            VALUES (?, ?, ?, ?, 'open', ?)
        ''', (ticket_id, str(interaction.user.id), str(seller.id) if seller else None, str(channel.id), ticket_type))
        conn.commit()
        
        order_id = f"RO-{uuid.uuid4().hex[:8].upper()}"
        try:
            price_float = float(price.replace('$', '').replace(CONFIG['currency_symbol'], '').strip())
        except:
            price_float = 0.0
        
        cursor.execute('''
            INSERT INTO orders (id, user_id, product_name, seller_id, seller_name, price, status, ticket_channel_id)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
        ''', (order_id, str(interaction.user.id), product, str(seller.id) if seller else None, seller_name, price_float, str(channel.id)))
        conn.commit()
        
        embed_title = get_setting(f'{ticket_type}_embed_title', f'🎫 {ticket_type.capitalize()} Purchase Ticket')
        embed_color = int(get_setting(f'{ticket_type}_embed_color', CONFIG['colors']['primary']))
        
        embed = discord.Embed(
            title=embed_title,
            description=f"**Ticket ID:** `{ticket_id}`\n**Order ID:** `{order_id}`",
            color=embed_color
        )
        embed.add_field(name="🛒 Product", value=product, inline=True)
        embed.add_field(name="💰 Price", value=f"{CONFIG['currency_symbol']}{price_float}", inline=True)
        embed.add_field(name="👤 Buyer", value=interaction.user.mention, inline=True)
        embed.add_field(name="👤 Seller", value=seller.mention if seller else seller_name or 'Unknown', inline=True)
        embed.add_field(name="📝 Details", value=details[:1024] if len(details) > 1024 else details, inline=False)
        embed.set_footer(text=f"🛒 RoStock • {ticket_type.capitalize()} Ticket")
        embed.timestamp = datetime.now()
        embed.set_thumbnail(url=ROSTOCK_LOGO)
        embed.set_image(url=ROSTOCK_BANNER)
        
        view = CloseTicketView(ticket_id)
        await channel.send(embed=embed, view=view)
        
        mentions = []
        if seller:
            mentions.append(seller.mention)
        if support_role_id:
            support_role = interaction.guild.get_role(support_role_id)
            if support_role:
                mentions.append(support_role.mention)
        
        if mentions:
            welcome_msg = f"📢 **New {ticket_type.capitalize()} purchase request!**\n\nPlease review the details above and assist the buyer.\nUse the buttons below to manage this ticket."
            await channel.send(f"{' '.join(mentions)}\n{welcome_msg}")
        
        confirm_embed = discord.Embed(
            title="✅ Ticket Created Successfully!",
            description=f"Your {ticket_type} purchase ticket has been created.\n\n**Ticket:** {channel.mention}\n**ID:** `{ticket_id}`\n**Order:** `{order_id}`",
            color=CONFIG['colors']['success']
        )
        confirm_embed.set_footer(text="🛒 RoStock • Staff will assist you shortly")
        confirm_embed.timestamp = datetime.now()
        confirm_embed.set_thumbnail(url=ROSTOCK_LOGO)
        confirm_embed.set_image(url=ROSTOCK_BANNER)
        
        await interaction.followup.send(embed=confirm_embed, ephemeral=True)
        
    except Exception as e:
        print(f"Ticket creation error: {e}")
        error_embed = discord.Embed(
            title="❌ Failed to Create Ticket",
            description=f"An error occurred while creating your ticket.\n\n**Error:** {str(e)}\n\nPlease contact an administrator.",
            color=CONFIG['colors']['error']
        )
        error_embed.set_footer(text="🛒 RoStock • Please try again or contact support")
        error_embed.set_thumbnail(url=ROSTOCK_LOGO)
        await interaction.followup.send(embed=error_embed, ephemeral=True)

async def close_ticket(interaction: discord.Interaction, ticket_id: str):
    cursor.execute('SELECT * FROM tickets WHERE ticket_id = ?', (ticket_id,))
    ticket = cursor.fetchone()
    
    if not ticket:
        await interaction.response.send_message('❌ Ticket not found.', ephemeral=True)
        return
    
    is_owner = str(interaction.user.id) == ticket[3]
    is_seller = str(interaction.user.id) == ticket[4] if ticket[4] else False
    is_admin = interaction.user.guild_permissions.administrator
    
    if not (is_owner or is_seller or is_admin):
        await interaction.response.send_message('❌ You do not have permission to close this ticket.', ephemeral=True)
        return
    
    cursor.execute('''
        UPDATE tickets 
        SET status = 'closed', 
            closed_at = CURRENT_TIMESTAMP,
            closed_by = ?
        WHERE ticket_id = ?
    ''', (str(interaction.user.id), ticket_id))
    conn.commit()
    
    channel = interaction.guild.get_channel(int(ticket[5]))
    
    embed = discord.Embed(
        title="🔒 Ticket Closed",
        description=f"This ticket has been closed by {interaction.user.mention}.\n\n**Ticket ID:** {ticket_id}\n**Closed at:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        color=CONFIG['colors']['error']
    )
    embed.set_footer(text="🛒 RoStock • Ticket closed")
    embed.timestamp = datetime.now()
    embed.set_thumbnail(url=ROSTOCK_LOGO)
    embed.set_image(url=ROSTOCK_BANNER)
    
    if channel:
        await channel.send(embed=embed)
        await channel.set_permissions(interaction.guild.default_role, view_channel=False)
    
    await interaction.response.send_message(f'✅ Ticket {ticket_id} has been closed.', ephemeral=True)
    log_action('ticket_closed', str(interaction.user.id), str(interaction.user.id), {'ticket_id': ticket_id})

async def send_transcript(interaction: discord.Interaction, ticket_id: str):
    cursor.execute('SELECT * FROM tickets WHERE ticket_id = ?', (ticket_id,))
    ticket = cursor.fetchone()
    
    if not ticket:
        await interaction.response.send_message('❌ Ticket not found.', ephemeral=True)
        return
    
    channel = interaction.guild.get_channel(int(ticket[5]))
    if not channel:
        await interaction.response.send_message('❌ Channel not found.', ephemeral=True)
        return
    
    transcript = []
    transcript.append("=" * 60)
    transcript.append(f"🛒 RoStock Ticket Transcript")
    transcript.append("=" * 60)
    transcript.append(f"Ticket ID: {ticket_id}")
    transcript.append(f"Type: {ticket[8] if len(ticket) > 8 else 'Purchase'}")
    transcript.append(f"Created: {ticket[6]}")
    transcript.append(f"User: {ticket[3]}")
    transcript.append(f"Seller: {ticket[4] or 'N/A'}")
    transcript.append("=" * 60)
    transcript.append("")
    
    try:
        async for message in channel.history(limit=100, oldest_first=True):
            timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
            author = message.author.display_name
            content = message.content or "[Embed or attachment]"
            transcript.append(f"[{timestamp}] {author}: {content}")
    except:
        pass
    
    transcript.append("")
    transcript.append("=" * 60)
    transcript.append(f"End of Transcript - Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    transcript_text = "\n".join(transcript)
    
    import io
    file = discord.File(io.StringIO(transcript_text), filename=f"transcript-{ticket_id}.txt")
    
    embed = discord.Embed(
        title="📋 Ticket Transcript",
        description=f"Transcript for **{ticket_id}**\n\nChannel: {channel.mention}\nMessages: {len(transcript) - 8}",
        color=CONFIG['colors']['info']
    )
    embed.set_footer(text="🛒 RoStock")
    embed.timestamp = datetime.now()
    embed.set_thumbnail(url=ROSTOCK_LOGO)
    embed.set_image(url=ROSTOCK_BANNER)
    
    await interaction.response.send_message(embed=embed, file=file, ephemeral=True)

# ============ PANEL CREATION ============

async def create_panel(channel, panel_type, custom_data=None):
    panel_name = panel_type.capitalize()
    
    embed_title = custom_data.get('embed_title') if custom_data else get_setting(f'{panel_type}_embed_title', f'🎫 {panel_name} Purchase Center')
    embed_desc = custom_data.get('embed_description') if custom_data else get_setting(f'{panel_type}_embed_description', f'Welcome to the **RoStock** {panel_name} purchase center!\n\nClick the button below to create a {panel_name} purchase ticket.\nOur support team will assist you with your purchase.')
    button_label = custom_data.get('button_label') if custom_data else get_setting(f'{panel_type}_button_label', f'🛒 Purchase {panel_name}')
    embed_color = int(custom_data.get('embed_color', CONFIG['colors']['primary'])) if custom_data else int(get_setting(f'{panel_type}_embed_color', CONFIG['colors']['primary']))
    
    embed = discord.Embed(
        title=embed_title,
        description=embed_desc,
        color=embed_color
    )
    embed.set_thumbnail(url=ROSTOCK_LOGO)
    embed.set_image(url=ROSTOCK_BANNER)
    embed.set_footer(text=f"🛒 RoStock • {panel_name} Panel")
    embed.timestamp = datetime.now()
    
    view = discord.ui.View()
    view.add_item(discord.ui.Button(
        label=button_label[:80],
        style=discord.ButtonStyle.primary,
        custom_id=f'create_{panel_type}_ticket',
        emoji='🎫'
    ))
    
    await channel.send(embed=embed, view=view)

# ============ SETUP WIZARD ============

async def setup_wizard(ctx):
    user_id = ctx.author.id
    guild = ctx.guild
    
    embed = discord.Embed(
        title="⚙️ RoStock Setup Wizard",
        description="**Step 1 of 5: Creating Ticket Category**\n\n"
                    "I'll create a **TICKETS** category for all ticket channels.",
        color=CONFIG['colors']['primary']
    )
    embed.set_thumbnail(url=ROSTOCK_LOGO)
    embed.set_image(url=ROSTOCK_BANNER)
    await ctx.send(embed=embed)
    
    try:
        category = await guild.create_category("TICKETS")
        save_setting('ticket_category_id', str(category.id))
        CONFIG['ticket_category_id'] = category.id
        
        embed = discord.Embed(
            title="✅ Category Created!",
            description=f"Created category: **{category.name}**",
            color=CONFIG['colors']['success']
        )
        embed.set_thumbnail(url=ROSTOCK_LOGO)
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ Failed to create category: {e}")
        return
    
    embed = discord.Embed(
        title="⚙️ RoStock Setup Wizard",
        description="**Step 2 of 5: Support Role**\n\n"
                    "Please mention the role that should be pinged for new tickets.\n"
                    "Example: `@Support` or type `none` to skip.",
        color=CONFIG['colors']['primary']
    )
    embed.set_thumbnail(url=ROSTOCK_LOGO)
    embed.set_image(url=ROSTOCK_BANNER)
    await ctx.send(embed=embed)
    
    def check(m):
        return m.author.id == user_id and m.channel.id == ctx.channel.id
    
    try:
        msg = await bot.wait_for('message', timeout=60.0, check=check)
        if msg.content.lower() != 'none':
            role_name = msg.content.replace('@', '').strip()
            role = discord.utils.get(guild.roles, name=role_name)
            if not role:
                for r in guild.roles:
                    if r.mention in msg.content:
                        role = r
                        break
            
            if role:
                save_setting('support_role_id', str(role.id))
                CONFIG['support_role_id'] = role.id
                await ctx.send(f"✅ Support role set to: {role.mention}")
            else:
                await ctx.send("❌ Role not found. Please run setup again to set it.")
    except asyncio.TimeoutError:
        await ctx.send("⏰ Timeout! Skipping support role.")
    
    embed = discord.Embed(
        title="⚙️ RoStock Setup Wizard",
        description="**Step 3 of 5: Admin Role**\n\n"
                    "Please mention the role for admins.\n"
                    "Example: `@Admin` or type `none` to skip.",
        color=CONFIG['colors']['primary']
    )
    embed.set_thumbnail(url=ROSTOCK_LOGO)
    embed.set_image(url=ROSTOCK_BANNER)
    await ctx.send(embed=embed)
    
    try:
        msg = await bot.wait_for('message', timeout=60.0, check=check)
        if msg.content.lower() != 'none':
            role_name = msg.content.replace('@', '').strip()
            role = discord.utils.get(guild.roles, name=role_name)
            if not role:
                for r in guild.roles:
                    if r.mention in msg.content:
                        role = r
                        break
            
            if role:
                save_setting('admin_role_id', str(role.id))
                CONFIG['admin_role_id'] = role.id
                await ctx.send(f"✅ Admin role set to: {role.mention}")
            else:
                await ctx.send("❌ Role not found. Please run setup again to set it.")
    except asyncio.TimeoutError:
        await ctx.send("⏰ Timeout! Skipping admin role.")
    
    # Deco Panel
    embed = discord.Embed(
        title="⚙️ RoStock Setup Wizard",
        description="**Step 4 of 5: Deco Panel Configuration**\n\n"
                    "Please enter the **panel title** for the Deco panel.\n"
                    "(e.g., '🎨 Deco Purchase Center')",
        color=CONFIG['colors']['primary']
    )
    embed.set_thumbnail(url=ROSTOCK_LOGO)
    embed.set_image(url=ROSTOCK_BANNER)
    await ctx.send(embed=embed)
    
    try:
        msg = await bot.wait_for('message', timeout=60.0, check=check)
        deco_title = msg.content
        
        embed = discord.Embed(
            title="⚙️ RoStock Setup Wizard",
            description="**Step 4 of 5: Deco Panel Configuration**\n\n"
                        "Please enter the **panel description** for the Deco panel.",
            color=CONFIG['colors']['primary']
        )
        embed.set_thumbnail(url=ROSTOCK_LOGO)
        embed.set_image(url=ROSTOCK_BANNER)
        await ctx.send(embed=embed)
        
        msg = await bot.wait_for('message', timeout=60.0, check=check)
        deco_desc = msg.content
        
        embed = discord.Embed(
            title="⚙️ RoStock Setup Wizard",
            description="**Step 4 of 5: Deco Panel Configuration**\n\n"
                        "Please enter the **button text** for the Deco panel.\n"
                        "(e.g., '🛒 Purchase Deco')",
            color=CONFIG['colors']['primary']
        )
        embed.set_thumbnail(url=ROSTOCK_LOGO)
        embed.set_image(url=ROSTOCK_BANNER)
        await ctx.send(embed=embed)
        
        msg = await bot.wait_for('message', timeout=60.0, check=check)
        deco_button = msg.content
        
        save_setting('deco_embed_title', deco_title)
        save_setting('deco_embed_description', deco_desc)
        save_setting('deco_button_label', deco_button)
        
        await ctx.send("✅ Deco panel configured!")
    except asyncio.TimeoutError:
        await ctx.send("⏰ Timeout! Using default Deco settings.")
    
    # Nitro Panel
    embed = discord.Embed(
        title="⚙️ RoStock Setup Wizard",
        description="**Step 5 of 5: Nitro Panel Configuration**\n\n"
                    "Please enter the **panel title** for the Nitro panel.\n"
                    "(e.g., '💎 Nitro Purchase Center')",
        color=CONFIG['colors']['primary']
    )
    embed.set_thumbnail(url=ROSTOCK_LOGO)
    embed.set_image(url=ROSTOCK_BANNER)
    await ctx.send(embed=embed)
    
    try:
        msg = await bot.wait_for('message', timeout=60.0, check=check)
        nitro_title = msg.content
        
        embed = discord.Embed(
            title="⚙️ RoStock Setup Wizard",
            description="**Step 5 of 5: Nitro Panel Configuration**\n\n"
                        "Please enter the **panel description** for the Nitro panel.",
            color=CONFIG['colors']['primary']
        )
        embed.set_thumbnail(url=ROSTOCK_LOGO)
        embed.set_image(url=ROSTOCK_BANNER)
        await ctx.send(embed=embed)
        
        msg = await bot.wait_for('message', timeout=60.0, check=check)
        nitro_desc = msg.content
        
        embed = discord.Embed(
            title="⚙️ RoStock Setup Wizard",
            description="**Step 5 of 5: Nitro Panel Configuration**\n\n"
                        "Please enter the **button text** for the Nitro panel.\n"
                        "(e.g., '🛒 Purchase Nitro')",
            color=CONFIG['colors']['primary']
        )
        embed.set_thumbnail(url=ROSTOCK_LOGO)
        embed.set_image(url=ROSTOCK_BANNER)
        await ctx.send(embed=embed)
        
        msg = await bot.wait_for('message', timeout=60.0, check=check)
        nitro_button = msg.content
        
        save_setting('nitro_embed_title', nitro_title)
        save_setting('nitro_embed_description', nitro_desc)
        save_setting('nitro_button_label', nitro_button)
        
        await ctx.send("✅ Nitro panel configured!")
    except asyncio.TimeoutError:
        await ctx.send("⏰ Timeout! Using default Nitro settings.")
    
    support_role_id = get_setting('support_role_id', 0)
    admin_role_id = get_setting('admin_role_id', 0)
    support_text = f'<@&{support_role_id}>' if support_role_id else 'None'
    admin_text = f'<@&{admin_role_id}>' if admin_role_id else 'None'
    
    final_embed = discord.Embed(
        title="✅ Setup Complete!",
        description="Your RoStock bot has been configured successfully!\n\n"
                    "**What was set up:**\n"
                    f"• ✅ Created **TICKETS** category\n"
                    f"• ✅ Support role: {support_text}\n"
                    f"• ✅ Admin role: {admin_text}\n"
                    f"• ✅ Deco panel configured\n"
                    f"• ✅ Nitro panel configured\n\n"
                    "**Use these commands to create panels:**\n"
                    "• `,decopanel` - Create Deco purchase panel\n"
                    "• `,nitropanel` - Create Nitro purchase panel\n\n"
                    "**Tickets will now be created in the TICKETS category!**",
        color=CONFIG['colors']['success']
    )
    final_embed.set_thumbnail(url=ROSTOCK_LOGO)
    final_embed.set_image(url=ROSTOCK_BANNER)
    final_embed.set_footer(text="🛒 RoStock • Ready to use!")
    await ctx.send(embed=final_embed)

# ============ PREFIX COMMANDS ============

@bot.command(name='setup')
async def setup_prefix(ctx):
    if not ctx.author.guild_permissions.administrator:
        await ctx.send('❌ You need administrator permissions to run this command.')
        return
    await setup_wizard(ctx)

@bot.command(name='decopanel')
async def decopanel_prefix(ctx, channel: discord.TextChannel = None):
    if not ctx.author.guild_permissions.administrator:
        await ctx.send('❌ You need administrator permissions to run this command.')
        return
    channel = channel or ctx.channel
    await create_panel(channel, 'deco')
    await ctx.send(f'✅ Deco panel created in {channel.mention}')

@bot.command(name='nitropanel')
async def nitropanel_prefix(ctx, channel: discord.TextChannel = None):
    if not ctx.author.guild_permissions.administrator:
        await ctx.send('❌ You need administrator permissions to run this command.')
        return
    channel = channel or ctx.channel
    await create_panel(channel, 'nitro')
    await ctx.send(f'✅ Nitro panel created in {channel.mention}')

@bot.command(name='help')
async def help_prefix(ctx):
    embed = discord.Embed(
        title="📚 RoStock Commands",
        description="Welcome to **RoStock**! Here are all available commands.",
        color=CONFIG['colors']['primary']
    )
    embed.set_thumbnail(url=ROSTOCK_LOGO)
    embed.set_image(url=ROSTOCK_BANNER)
    embed.set_footer(text="🛒 RoStock • Use , before each command")
    embed.timestamp = datetime.now()
    
    embed.add_field(
        name="🛒 **Shop Commands**",
        value="`,shop` - Browse the shop\n"
              "`,balance` or `,bal` - Check your balance\n"
              "`,buy <product>` - Purchase a product\n"
              "`,stock` - View current stock\n"
              "`,orders` - View your orders\n"
              "`,product <name>` - View product details",
        inline=False
    )
    
    embed.add_field(
        name="🎫 **Ticket Commands**",
        value="`,decopanel` - Create Deco purchase panel (Admin)\n"
              "`,nitropanel` - Create Nitro purchase panel (Admin)\n"
              "`,setup` - Run setup wizard (Admin)\n"
              "`,ticket` - Create a purchase ticket\n"
              "`,support` - Open support ticket\n"
              "`,cancel <order>` - Cancel an order",
        inline=False
    )
    
    embed.add_field(
        name="🔧 **Utility Commands**",
        value="`,say <message>` - Make the bot say something\n"
              "`,help` - Show this help menu",
        inline=False
    )
    
    await ctx.send(embed=embed)

@bot.command(name='say')
async def say_prefix(ctx, *, message):
    await ctx.send(message)

@bot.command(name='shop')
async def shop_prefix(ctx):
    embed = discord.Embed(
        title="🛍️ RoStock Shop",
        description="Select a category below.",
        color=CONFIG['colors']['primary']
    )
    embed.set_thumbnail(url=ROSTOCK_LOGO)
    embed.set_image(url=ROSTOCK_BANNER)
    embed.set_footer(text="🛒 RoStock")
    embed.timestamp = datetime.now()
    view = discord.ui.View()
    for cat in CONFIG['categories']:
        view.add_item(discord.ui.Button(
            label=cat,
            style=discord.ButtonStyle.primary,
            custom_id=f'shop_category_{cat}'
        ))
    await ctx.send(embed=embed, view=view)

@bot.command(name='balance', aliases=['bal'])
async def balance_prefix(ctx):
    ensure_user(ctx.author.id)
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (ctx.author.id,))
    user = cursor.fetchone()
    embed = discord.Embed(
        title="💰 Your Balance",
        color=CONFIG['colors']['success']
    )
    embed.add_field(name="Available", value=f"{CONFIG['currency_symbol']}{user[1]:.2f}", inline=True)
    embed.add_field(name="Total Spent", value=f"{CONFIG['currency_symbol']}{user[2]:.2f}", inline=True)
    embed.add_field(name="Orders", value=str(user[3]), inline=True)
    embed.set_thumbnail(url=ROSTOCK_LOGO)
    embed.set_image(url=ROSTOCK_BANNER)
    embed.set_footer(text="🛒 RoStock")
    embed.timestamp = datetime.now()
    await ctx.send(embed=embed)

@bot.command(name='buy')
async def buy_prefix(ctx, *, product_name):
    cursor.execute('SELECT * FROM products WHERE name LIKE ? AND active = 1', (f'%{product_name}%',))
    product_data = cursor.fetchone()
    if not product_data:
        await ctx.send('❌ Product not found.')
        return
    if product_data[4] < 1:
        await ctx.send('❌ Out of stock.')
        return
    order_id = f"RO-{uuid.uuid4().hex[:8].upper()}"
    expires = (datetime.now() + timedelta(minutes=30)).isoformat()
    cursor.execute('''
        INSERT INTO orders (id, user_id, product_id, product_name, seller_id, seller_name, price, quantity, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
    ''', (order_id, ctx.author.id, product_data[0], product_data[1], product_data[7], product_data[8], product_data[2], expires))
    conn.commit()
    ensure_user(ctx.author.id)
    embed = discord.Embed(
        title="🛒 Order Created",
        description=f"**Order ID:** `{order_id}`\n**Product:** {product_data[1]}\n**Price:** {CONFIG['currency_symbol']}{product_data[2]}\n\nUse `/pay {order_id}` to complete payment",
        color=CONFIG['colors']['success']
    )
    embed.set_thumbnail(url=ROSTOCK_LOGO)
    embed.set_image(url=ROSTOCK_BANNER)
    embed.set_footer(text="🛒 RoStock")
    embed.timestamp = datetime.now()
    await ctx.send(embed=embed)

@bot.command(name='stock')
async def stock_prefix(ctx):
    cursor.execute('SELECT name, stock, price, category FROM products WHERE active = 1 ORDER BY category, name')
    products = cursor.fetchall()
    if not products:
        await ctx.send('No products available.')
        return
    embed = discord.Embed(
        title="📦 RoStock Inventory",
        color=CONFIG['colors']['info']
    )
    desc = ''
    last_cat = ''
    for p in products:
        if p[3] != last_cat:
            last_cat = p[3]
            desc += f'\n**{last_cat}**\n'
        desc += f"• **{p[0]}** — {CONFIG['currency_symbol']}{p[2]} | Stock: **{p[1]}**\n"
    embed.description = desc[:4090]
    embed.set_thumbnail(url=ROSTOCK_LOGO)
    embed.set_image(url=ROSTOCK_BANNER)
    embed.set_footer(text="🛒 RoStock")
    embed.timestamp = datetime.now()
    await ctx.send(embed=embed)

@bot.command(name='orders')
async def orders_prefix(ctx):
    cursor.execute(
        'SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC LIMIT 15',
        (ctx.author.id,)
    )
    orders_data = cursor.fetchall()
    if not orders_data:
        await ctx.send('You have no orders.')
        return
    embed = discord.Embed(
        title="📜 Your Orders",
        color=CONFIG['colors']['primary']
    )
    desc = ''
    for o in orders_data:
        desc += f"**{o[0]}** — {o[3]}\n{CONFIG['currency_symbol']}{o[5]} | `{o[6]}`\n\n"
    embed.description = desc
    embed.set_thumbnail(url=ROSTOCK_LOGO)
    embed.set_image(url=ROSTOCK_BANNER)
    embed.set_footer(text="🛒 RoStock")
    embed.timestamp = datetime.now()
    await ctx.send(embed=embed)

@bot.command(name='product')
async def product_prefix(ctx, *, product_name):
    cursor.execute('SELECT * FROM products WHERE name LIKE ? AND active = 1', (f'%{product_name}%',))
    product_data = cursor.fetchone()
    if not product_data:
        await ctx.send('❌ Product not found.')
        return
    embed = discord.Embed(
        title=product_data[1],
        description=product_data[3] or '*No description*',
        color=CONFIG['colors']['primary']
    )
    embed.add_field(name="💰 Price", value=f"{CONFIG['currency_symbol']}{product_data[2]}", inline=True)
    embed.add_field(name="📦 Stock", value=str(product_data[4]), inline=True)
    embed.add_field(name="📁 Category", value=product_data[5], inline=True)
    embed.set_thumbnail(url=ROSTOCK_LOGO)
    embed.set_image(url=ROSTOCK_BANNER)
    embed.set_footer(text="🛒 RoStock")
    embed.timestamp = datetime.now()
    await ctx.send(embed=embed)

@bot.command(name='ticket')
async def ticket_prefix(ctx):
    embed = discord.Embed(
        title="🎫 RoStock Purchase Ticket",
        description="Click the button below to create a purchase ticket.\n"
                    "Please fill out the form completely.",
        color=CONFIG['colors']['primary']
    )
    embed.set_thumbnail(url=ROSTOCK_LOGO)
    embed.set_image(url=ROSTOCK_BANNER)
    embed.set_footer(text="🛒 RoStock")
    embed.timestamp = datetime.now()
    view = TicketView()
    await ctx.send(embed=embed, view=view)

@bot.command(name='support')
async def support_prefix(ctx):
    embed = discord.Embed(
        title="🎫 RoStock Support",
        description="Click the button below to create a support ticket.",
        color=CONFIG['colors']['info']
    )
    embed.set_thumbnail(url=ROSTOCK_LOGO)
    embed.set_image(url=ROSTOCK_BANNER)
    embed.set_footer(text="🛒 RoStock Support")
    embed.timestamp = datetime.now()
    view = TicketView(ticket_type='support')
    await ctx.send(embed=embed, view=view)

@bot.command(name='cancel')
async def cancel_prefix(ctx, order_id: str):
    order_id = order_id.upper()
    cursor.execute('SELECT * FROM orders WHERE id = ? AND user_id = ?', (order_id, ctx.author.id))
    order_data = cursor.fetchone()
    if not order_data:
        await ctx.send('❌ Order not found.')
        return
    if order_data[6] != 'pending':
        await ctx.send(f'❌ Only pending orders can be cancelled. Current status: {order_data[6]}')
        return
    cursor.execute('UPDATE orders SET status = "cancelled" WHERE id = ?', (order_id,))
    conn.commit()
    await ctx.send(f'✅ Order `{order_id}` cancelled.')

# ============ EVENT HANDLERS ============

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if message.content.startswith(','):
        try:
            await message.delete()
        except:
            pass
    await bot.process_commands(message)

@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type == discord.InteractionType.component:
        custom_id = interaction.data.get('custom_id', '')
        
        if custom_id.startswith('create_') and custom_id.endswith('_ticket'):
            ticket_type = custom_id.replace('create_', '').replace('_ticket', '')
            modal = PurchaseModal(ticket_type=ticket_type)
            await interaction.response.send_modal(modal)
            return
        
        if custom_id == 'close_ticket':
            ticket_id = None
            cursor.execute('SELECT ticket_id FROM tickets WHERE channel_id = ?', (str(interaction.channel.id),))
            result = cursor.fetchone()
            if result:
                ticket_id = result[0]
            if ticket_id:
                await close_ticket(interaction, ticket_id)
            return
        
        if custom_id == 'transcript_ticket':
            ticket_id = None
            cursor.execute('SELECT ticket_id FROM tickets WHERE channel_id = ?', (str(interaction.channel.id),))
            result = cursor.fetchone()
            if result:
                ticket_id = result[0]
            if ticket_id:
                await send_transcript(interaction, ticket_id)
            return
        
        if custom_id.startswith('shop_category_'):
            category = custom_id.replace('shop_category_', '')
            cursor.execute(
                'SELECT * FROM products WHERE category = ? AND active = 1 AND stock > 0 ORDER BY name',
                (category,)
            )
            products = cursor.fetchall()
            
            if not products:
                await interaction.response.send_message('No products in this category.', ephemeral=True)
                return
            
            select = discord.ui.Select(
                placeholder='Select a product',
                custom_id='shop_select_product'
            )
            for p in products[:25]:
                select.add_option(
                    label=p[1][:100],
                    description=f'{CONFIG["currency_symbol"]}{p[2]} • Stock: {p[4]}'[:100],
                    value=str(p[0])
                )
            
            view = discord.ui.View()
            view.add_item(select)
            
            embed = discord.Embed(
                title=f"🛍️ {category}",
                description="Choose a product from the menu below.",
                color=CONFIG['colors']['primary']
            )
            embed.set_thumbnail(url=ROSTOCK_LOGO)
            embed.set_image(url=ROSTOCK_BANNER)
            embed.set_footer(text="🛒 RoStock")
            embed.timestamp = datetime.now()
            await interaction.response.edit_message(embed=embed, view=view)
            return
        
        if custom_id == 'shop_select_product':
            selected = interaction.data.get('values', [''])[0]
            cursor.execute('SELECT * FROM products WHERE id = ?', (selected,))
            product_data = cursor.fetchone()
            
            if not product_data:
                await interaction.response.send_message('Product not found.', ephemeral=True)
                return
            
            embed = discord.Embed(
                title=product_data[1],
                description=product_data[3] or '*No description*',
                color=CONFIG['colors']['primary']
            )
            embed.add_field(name="💰 Price", value=f"{CONFIG['currency_symbol']}{product_data[2]}", inline=True)
            embed.add_field(name="📦 Stock", value=str(product_data[4]), inline=True)
            embed.add_field(name="📁 Category", value=product_data[5], inline=True)
            embed.add_field(name="👤 Seller", value=product_data[8] or 'None', inline=True)
            embed.set_thumbnail(url=ROSTOCK_LOGO)
            embed.set_image(url=ROSTOCK_BANNER)
            embed.set_footer(text="🛒 RoStock")
            embed.timestamp = datetime.now()
            
            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                label='🛒 Buy Now',
                style=discord.ButtonStyle.success,
                custom_id=f'buy_now_{product_data[0]}'
            ))
            view.add_item(discord.ui.Button(
                label='← Back',
                style=discord.ButtonStyle.secondary,
                custom_id='shop_back'
            ))
            
            await interaction.response.edit_message(embed=embed, view=view)
            return
        
        if custom_id == 'shop_back':
            embed = discord.Embed(
                title="🛍️ RoStock Shop",
                description="Select a category below.",
                color=CONFIG['colors']['primary']
            )
            embed.set_thumbnail(url=ROSTOCK_LOGO)
            embed.set_image(url=ROSTOCK_BANNER)
            embed.set_footer(text="🛒 RoStock")
            embed.timestamp = datetime.now()
            view = discord.ui.View()
            for cat in CONFIG['categories']:
                view.add_item(discord.ui.Button(
                    label=cat,
                    style=discord.ButtonStyle.primary,
                    custom_id=f'shop_category_{cat}'
                ))
            await interaction.response.edit_message(embed=embed, view=view)
            return
        
        if custom_id.startswith('buy_now_'):
            product_id = custom_id.replace('buy_now_', '')
            cursor.execute('SELECT * FROM products WHERE id = ? AND active = 1', (product_id,))
            product_data = cursor.fetchone()
            
            if not product_data or product_data[4] < 1:
                await interaction.response.send_message('❌ Out of stock.', ephemeral=True)
                return
            
            order_id = f"RO-{uuid.uuid4().hex[:8].upper()}"
            expires = (datetime.now() + timedelta(minutes=30)).isoformat()
            
            cursor.execute('''
                INSERT INTO orders (id, user_id, product_id, product_name, seller_id, seller_name, price, quantity, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            ''', (order_id, interaction.user.id, product_data[0], product_data[1], product_data[7], product_data[8], product_data[2], expires))
            conn.commit()
            ensure_user(interaction.user.id)
            log_action('order_created', str(interaction.user.id), data={'order_id': order_id, 'product': product_data[1]})
            
            embed = discord.Embed(
                title="🛒 Order Created",
                description=f"**Order ID:** `{order_id}`\n**Product:** {product_data[1]}\n**Price:** {CONFIG['currency_symbol']}{product_data[2]}\n**Seller:** {product_data[8] or 'None'}\n\nClick **Pay Now** or use `/pay {order_id}`",
                color=CONFIG['colors']['success']
            )
            embed.set_thumbnail(url=ROSTOCK_LOGO)
            embed.set_image(url=ROSTOCK_BANNER)
            embed.set_footer(text="🛒 RoStock")
            embed.timestamp = datetime.now()
            
            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                label='💳 Pay Now',
                style=discord.ButtonStyle.primary,
                custom_id=f'pay_btn_{order_id}'
            ))
            view.add_item(discord.ui.Button(
                label='❌ Cancel',
                style=discord.ButtonStyle.danger,
                custom_id=f'cancel_order_{order_id}'
            ))
            
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            return
        
        if custom_id.startswith('pay_btn_'):
            order_id = custom_id.replace('pay_btn_', '')
            cursor.execute('SELECT * FROM orders WHERE id = ? AND user_id = ?', (order_id, interaction.user.id))
            order_data = cursor.fetchone()
            
            if not order_data:
                await interaction.response.send_message('Order not found.', ephemeral=True)
                return
            if order_data[6] != 'pending':
                await interaction.response.send_message(f'Order is already {order_data[6]}.', ephemeral=True)
                return
            
            embed = discord.Embed(
                title=f"💳 Payment • {order_id}",
                description=f"**Product:** {order_data[3]}\n**Amount due:** {CONFIG['currency_symbol']}{order_data[5]}\n**Seller:** {order_data[4] or 'None'}\n\n**Payment Methods:**\n• Account Balance\n• Crypto (ask seller)\n\nAfter payment use `/checkpayment {order_id}`",
                color=CONFIG['colors']['info']
            )
            embed.set_thumbnail(url=ROSTOCK_LOGO)
            embed.set_image(url=ROSTOCK_BANNER)
            embed.set_footer(text="🛒 RoStock")
            embed.timestamp = datetime.now()
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        if custom_id.startswith('cancel_order_'):
            order_id = custom_id.replace('cancel_order_', '')
            cursor.execute('SELECT * FROM orders WHERE id = ? AND user_id = ?', (order_id, interaction.user.id))
            order_data = cursor.fetchone()
            
            if not order_data:
                await interaction.response.send_message('Order not found.', ephemeral=True)
                return
            if order_data[6] != 'pending':
                await interaction.response.send_message('Only pending orders can be cancelled.', ephemeral=True)
                return
            
            cursor.execute('UPDATE orders SET status = "cancelled" WHERE id = ?', (order_id,))
            conn.commit()
            await interaction.response.send_message(f'✅ Order `{order_id}` cancelled.', ephemeral=True)
            return

@bot.event
async def on_submit(interaction: discord.Interaction):
    if interaction.type == discord.InteractionType.modal_submit:
        if hasattr(interaction, 'modal') and isinstance(interaction.modal, PurchaseModal):
            await handle_ticket_creation(interaction, interaction.modal)

# ============ RUN BOT ============
if __name__ == '__main__':
    print("🚀 Starting RoStock Discord Bot...")
    print(f"📊 Database: {DB_PATH}")
    print("📝 Prefix: , (comma)")
    print("🔗 Slash commands: / (also available)")
    try:
        bot.run(TOKEN)
    except Exception as e:
        print(f"❌ Failed to start bot: {e}")
