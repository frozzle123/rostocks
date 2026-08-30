# main.py - Complete Discord Shop Bot with Ticket System
import os
import json
import sqlite3
import uuid
import asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv
import discord
from discord import app_commands
from discord.ext import commands

# Load environment variables
load_dotenv()

# ============ CONFIGURATION ============
TOKEN = os.getenv('BOT_TOKEN')
CLIENT_ID = os.getenv('CLIENT_ID')
GUILD_ID = os.getenv('GUILD_ID')
OWNER_ID = os.getenv('OWNER_ID')

# Bot Configuration
CONFIG = {
    'currency_symbol': os.getenv('CURRENCY_SYMBOL', '$'),
    'order_prefix': os.getenv('ORDER_PREFIX', 'ORD-'),
    'payment_timeout': int(os.getenv('PAYMENT_TIMEOUT_MINUTES', 30)),
    'categories': os.getenv('CATEGORIES', 'STREAMING,DISCORD,GAMING,DIGITAL,ACCOUNTS,BOTS').split(','),
    
    # Ticket System Configuration
    'ticket_category_id': int(os.getenv('TICKET_CATEGORY_ID', 0)),  # Set this in Railway
    'support_role_id': int(os.getenv('SUPPORT_ROLE_ID', 0)),  # Set this in Railway
    'admin_role_id': int(os.getenv('ADMIN_ROLE_ID', 0)),  # Set this in Railway
    'ticket_log_channel_id': int(os.getenv('TICKET_LOG_CHANNEL_ID', 0)),  # Set this in Railway
    
    # Colors
    'colors': {
        'primary': int(os.getenv('COLOR_PRIMARY', '0x5865F2'), 16),
        'success': int(os.getenv('COLOR_SUCCESS', '0x57F287'), 16),
        'error': int(os.getenv('COLOR_ERROR', '0xED4245'), 16),
        'warning': int(os.getenv('COLOR_WARNING', '0xFEE75C'), 16),
        'info': int(os.getenv('COLOR_INFO', '0xEB459E'), 16),
    }
}

# Database setup
DB_PATH = os.getenv('DATABASE_PATH', 'shop.db')
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Create all tables
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

# ============ DISCORD BOT SETUP ============
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

class ShopBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix='!', intents=intents)
        self.db = conn
        self.config = CONFIG
        self.synced = False
        self.ticket_views = {}

    async def setup_hook(self):
        if not self.synced:
            await self.tree.sync(guild=discord.Object(id=int(GUILD_ID)))
            self.synced = True
            print(f"✅ Synced commands to guild {GUILD_ID}")

bot = ShopBot()

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

def create_embed(title, description, color='primary', fields=None, footer=None):
    embed = discord.Embed(
        title=title,
        description=description,
        color=CONFIG['colors'][color] if color in CONFIG['colors'] else CONFIG['colors']['primary']
    )
    if fields:
        for field in fields:
            embed.add_field(name=field['name'], value=field['value'], inline=field.get('inline', True))
    if footer:
        embed.set_footer(text=footer)
    embed.timestamp = datetime.now()
    return embed

async def deliver_order(order_id):
    cursor.execute('SELECT * FROM orders WHERE id = ?', (order_id,))
    order = cursor.fetchone()
    if not order or order[6] == 'delivered':  # status index
        return False

    cursor.execute('SELECT * FROM products WHERE id = ?', (order[2],))
    product = cursor.fetchone()

    if product and product[5] > 0:  # stock
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
    
    # Try to DM user
    try:
        user = await bot.fetch_user(order[1])
        embed = create_embed(
            '✅ Order Delivered',
            f"**Order ID:** `{order[0]}`\n**Product:** {order[3]}\n\n**Delivery:**\n```\n{product[6] if product else 'Contact support'}\n```",
            'success'
        )
        await user.send(embed=embed)
    except:
        pass
    
    return True

# ============ TICKET SYSTEM ============
class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label='🛒 Purchase Ticket', style=discord.ButtonStyle.primary, custom_id='create_ticket')
    async def create_ticket_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await create_ticket_modal(interaction)

class CloseTicketView(discord.ui.View):
    def __init__(self, ticket_id):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
    
    @discord.ui.button(label='🔒 Close Ticket', style=discord.ButtonStyle.danger, custom_id='close_ticket')
    async def close_ticket_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await close_ticket(interaction, self.ticket_id)
    
    @discord.ui.button(label='📋 Transcript', style=discord.ButtonStyle.secondary, custom_id='transcript_ticket')
    async def transcript_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await send_transcript(interaction, self.ticket_id)

class PurchaseModal(discord.ui.Modal, title='🛒 New Purchase Request'):
    def __init__(self):
        super().__init__()
    
    product = discord.ui.TextInput(
        label='What product/service do you want to purchase?',
        placeholder='e.g., Netflix Account, Discord Nitro, etc.',
        required=True,
        style=discord.TextStyle.paragraph,
        max_length=200
    )
    
    seller = discord.ui.TextInput(
        label='Who is the seller? (Discord username or ID)',
        placeholder='e.g., @seller or their Discord ID',
        required=True,
        max_length=100
    )
    
    price = discord.ui.TextInput(
        label='What is the price?',
        placeholder='e.g., $50 or 50',
        required=True,
        max_length=50
    )
    
    details = discord.ui.TextInput(
        label='Additional details (optional)',
        placeholder='Any specific requirements, delivery method, etc.',
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=500
    )

async def create_ticket_modal(interaction: discord.Interaction):
    modal = PurchaseModal()
    await interaction.response.send_modal(modal)

@bot.event
async def on_submit(interaction: discord.Interaction):
    if not isinstance(interaction, discord.Interaction):
        return
    
    if interaction.data.get('custom_id') == 'create_ticket':
        return
    
    if isinstance(interaction, discord.Interaction) and hasattr(interaction, 'modal'):
        if isinstance(interaction.modal, PurchaseModal):
            await handle_ticket_creation(interaction)

async def handle_ticket_creation(interaction: discord.Interaction):
    modal = interaction.modal
    product = modal.product.value
    seller_input = modal.seller.value
    price = modal.price.value
    details = modal.details.value or 'No additional details provided.'
    
    # Find seller
    seller = None
    seller_name = seller_input
    
    # Try to find seller by mention or ID
    if seller_input.startswith('<@') and seller_input.endswith('>'):
        seller_id = seller_input.replace('<@', '').replace('>', '').replace('!', '')
        try:
            seller = await bot.fetch_user(int(seller_id))
            seller_name = seller.display_name
        except:
            pass
    else:
        # Try to find by username
        for guild in bot.guilds:
            for member in guild.members:
                if seller_input.lower() in member.display_name.lower() or seller_input.lower() in member.name.lower():
                    seller = member
                    seller_name = member.display_name
                    break
            if seller:
                break
    
    if not seller:
        # Try to get by ID if it's a number
        try:
            seller = await bot.fetch_user(int(seller_input))
            seller_name = seller.display_name
        except:
            pass
    
    # Check if user is blacklisted
    cursor.execute('SELECT blacklisted FROM users WHERE user_id = ?', (interaction.user.id,))
    blacklisted = cursor.fetchone()
    if blacklisted and blacklisted[0] == 1:
        await interaction.response.send_message(
            '❌ You are blacklisted from this shop.',
            ephemeral=True
        )
        return
    
    # Create ticket channel
    ticket_id = f"TICKET-{uuid.uuid4().hex[:8].upper()}"
    
    # Get category
    category_id = CONFIG['ticket_category_id']
    category = interaction.guild.get_channel(category_id) if category_id else None
    
    # Create channel
    try:
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }
        
        if seller:
            overwrites[seller] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        
        # Add support role if configured
        if CONFIG['support_role_id']:
            support_role = interaction.guild.get_role(CONFIG['support_role_id'])
            if support_role:
                overwrites[support_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        
        # Add admin role if configured
        if CONFIG['admin_role_id']:
            admin_role = interaction.guild.get_role(CONFIG['admin_role_id'])
            if admin_role:
                overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        
        channel = await interaction.guild.create_text_channel(
            f"ticket-{interaction.user.display_name[:20]}-{ticket_id[-4:]}",
            category=category,
            overwrites=overwrites,
            topic=f"Purchase ticket for {interaction.user.display_name} - {ticket_id}"
        )
        
        # Save ticket to database
        cursor.execute('''
            INSERT INTO tickets (ticket_id, user_id, seller_id, channel_id, status)
            VALUES (?, ?, ?, ?, 'open')
        ''', (ticket_id, str(interaction.user.id), str(seller.id) if seller else None, str(channel.id)))
        conn.commit()
        
        # Create order
        order_id = f"{CONFIG['order_prefix']}{uuid.uuid4().hex[:8].upper()}"
        try:
            price_float = float(price.replace('$', '').replace(CONFIG['currency_symbol'], '').strip())
        except:
            price_float = 0.0
        
        cursor.execute('''
            INSERT INTO orders (id, user_id, product_name, seller_id, seller_name, price, status, ticket_channel_id)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
        ''', (order_id, str(interaction.user.id), product, str(seller.id) if seller else None, seller_name, price_float, str(channel.id)))
        conn.commit()
        
        # Create ticket embed
        embed = create_embed(
            f'🎫 New Purchase Ticket - {ticket_id}',
            f"**Ticket ID:** `{ticket_id}`\n"
            f"**Order ID:** `{order_id}`\n"
            f"**Buyer:** {interaction.user.mention}\n"
            f"**Seller:** {seller.mention if seller else seller_name or 'Unknown'}\n"
            f"**Product:** {product}\n"
            f"**Price:** {CONFIG['currency_symbol']}{price_float}\n\n"
            f"**Details:**\n{details}",
            'primary',
            footer=f"Created by {interaction.user.display_name}"
        )
        
        view = CloseTicketView(ticket_id)
        ticket_message = await channel.send(embed=embed, view=view)
        
        # Ping seller and support
        mentions = []
        if seller:
            mentions.append(seller.mention)
        if CONFIG['support_role_id']:
            support_role = interaction.guild.get_role(CONFIG['support_role_id'])
            if support_role:
                mentions.append(support_role.mention)
        
        if mentions:
            await channel.send(f"{' '.join(mentions)} - New purchase request!")
        
        # Log to ticket log channel
        if CONFIG['ticket_log_channel_id']:
            log_channel = interaction.guild.get_channel(CONFIG['ticket_log_channel_id'])
            if log_channel:
                log_embed = create_embed(
                    '🎫 New Ticket Created',
                    f"**Ticket:** {ticket_id}\n"
                    f"**Channel:** {channel.mention}\n"
                    f"**Buyer:** {interaction.user.mention}\n"
                    f"**Seller:** {seller.mention if seller else 'Unknown'}\n"
                    f"**Product:** {product}\n"
                    f"**Price:** {CONFIG['currency_symbol']}{price_float}",
                    'success'
                )
                await log_channel.send(embed=log_embed)
        
        log_action('ticket_created', str(interaction.user.id), str(seller.id) if seller else None, {
            'ticket_id': ticket_id,
            'order_id': order_id,
            'product': product,
            'price': price_float
        })
        
        await interaction.response.send_message(
            f'✅ Ticket created! Check {channel.mention}',
            ephemeral=True
        )
        
    except Exception as e:
        await interaction.response.send_message(
            f'❌ Failed to create ticket: {str(e)}',
            ephemeral=True
        )
        print(f"Ticket creation error: {e}")

async def close_ticket(interaction: discord.Interaction, ticket_id: str):
    # Check permissions
    cursor.execute('SELECT * FROM tickets WHERE ticket_id = ?', (ticket_id,))
    ticket = cursor.fetchone()
    
    if not ticket:
        await interaction.response.send_message('❌ Ticket not found.', ephemeral=True)
        return
    
    # Check if user has permission to close
    is_owner = str(interaction.user.id) == ticket[3]  # user_id
    is_seller = str(interaction.user.id) == ticket[4] if ticket[4] else False  # seller_id
    is_admin = interaction.user.guild_permissions.administrator
    
    if not (is_owner or is_seller or is_admin):
        await interaction.response.send_message('❌ You do not have permission to close this ticket.', ephemeral=True)
        return
    
    # Update ticket status
    cursor.execute('''
        UPDATE tickets 
        SET status = 'closed', 
            closed_at = CURRENT_TIMESTAMP,
            closed_by = ?
        WHERE ticket_id = ?
    ''', (str(interaction.user.id), ticket_id))
    conn.commit()
    
    # Get channel
    channel = interaction.guild.get_channel(int(ticket[5]))  # channel_id
    
    # Send closure message
    embed = create_embed(
        '🔒 Ticket Closed',
        f"This ticket has been closed by {interaction.user.mention}.\n"
        f"**Ticket ID:** {ticket_id}",
        'error'
    )
    
    if channel:
        await channel.send(embed=embed)
        # Update channel permissions
        await channel.set_permissions(interaction.guild.default_role, view_channel=False)
    
    await interaction.response.send_message(
        f'✅ Ticket {ticket_id} has been closed.',
        ephemeral=True
    )
    
    # Log closure
    log_action('ticket_closed', str(interaction.user.id), str(interaction.user.id), {
        'ticket_id': ticket_id
    })

async def send_transcript(interaction: discord.Interaction, ticket_id: str):
    cursor.execute('SELECT * FROM tickets WHERE ticket_id = ?', (ticket_id,))
    ticket = cursor.fetchone()
    
    if not ticket:
        await interaction.response.send_message('❌ Ticket not found.', ephemeral=True)
        return
    
    # Get messages from channel
    channel = interaction.guild.get_channel(int(ticket[5]))
    if not channel:
        await interaction.response.send_message('❌ Channel not found.', ephemeral=True)
        return
    
    transcript = []
    transcript.append(f"=== Ticket Transcript - {ticket_id} ===")
    transcript.append(f"Created: {ticket[6]}")
    transcript.append(f"User: {ticket[3]}")
    transcript.append(f"Seller: {ticket[4] or 'N/A'}")
    transcript.append("=" * 50)
    
    try:
        async for message in channel.history(limit=100, oldest_first=True):
            timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
            author = message.author.display_name
            content = message.content or "[Embed or attachment]"
            transcript.append(f"[{timestamp}] {author}: {content}")
    except:
        pass
    
    transcript_text = "\n".join(transcript)
    
    # Send as file
    import io
    file = discord.File(io.StringIO(transcript_text), filename=f"transcript-{ticket_id}.txt")
    
    await interaction.response.send_message(
        f'📋 Transcript for {ticket_id}:',
        file=file,
        ephemeral=True
    )

# ============ COMMANDS ============

# ---------- ADMIN COMMANDS ----------

@bot.tree.command(name='addproduct', description='Add a new product')
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    name='Product name',
    price='Product price',
    category='Product category',
    stock='Initial stock amount',
    description='Product description (optional)',
    delivery='Delivery data (optional)',
    seller='Seller Discord ID (optional)'
)
async def addproduct(
    interaction: discord.Interaction, 
    name: str, 
    price: float, 
    category: str, 
    stock: int, 
    description: str = '', 
    delivery: str = '',
    seller: str = ''
):
    try:
        cursor.execute('''
            INSERT INTO products (name, price, category, stock, description, delivery_data, seller_id, seller_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (name, price, category.upper(), stock, description, delivery, seller, ''))
        conn.commit()
        
        embed = create_embed(
            '✅ Product Added',
            f"**{name}**\n"
            f"Price: {CONFIG['currency_symbol']}{price}\n"
            f"Stock: {stock}\n"
            f"Category: {category}\n"
            f"Seller: {seller or 'None'}",
            'success'
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except sqlite3.IntegrityError:
        await interaction.response.send_message('❌ Product name already exists.', ephemeral=True)

@bot.tree.command(name='addstock', description='Add stock to a product')
@app_commands.default_permissions(administrator=True)
@app_commands.describe(name='Product name', amount='Amount to add')
async def addstock(interaction: discord.Interaction, name: str, amount: int):
    cursor.execute('SELECT * FROM products WHERE name LIKE ?', (f'%{name}%',))
    product = cursor.fetchone()
    if not product:
        await interaction.response.send_message('❌ Product not found.', ephemeral=True)
        return
    
    cursor.execute('UPDATE products SET stock = stock + ? WHERE id = ?', (amount, product[0]))
    conn.commit()
    await interaction.response.send_message(
        f'✅ Added {amount} stock to **{product[1]}**. New stock: {product[4] + amount}',
        ephemeral=True
    )

@bot.tree.command(name='blacklist', description='Blacklist a user')
@app_commands.default_permissions(administrator=True)
@app_commands.describe(user='User to blacklist', reason='Reason for blacklist')
async def blacklist(interaction: discord.Interaction, user: discord.User, reason: str = 'No reason provided'):
    ensure_user(user.id)
    cursor.execute('UPDATE users SET blacklisted = 1, blacklist_reason = ? WHERE user_id = ?', (reason, user.id))
    conn.commit()
    log_action('blacklist_user', str(user.id), str(interaction.user.id), {'reason': reason})
    await interaction.response.send_message(
        f'✅ {user.mention} has been blacklisted.\nReason: {reason}',
        ephemeral=True
    )

@bot.tree.command(name='unblacklist', description='Remove user from blacklist')
@app_commands.default_permissions(administrator=True)
@app_commands.describe(user='User to unblacklist')
async def unblacklist(interaction: discord.Interaction, user: discord.User):
    cursor.execute('UPDATE users SET blacklisted = 0, blacklist_reason = NULL WHERE user_id = ?', (user.id,))
    conn.commit()
    log_action('unblacklist_user', str(user.id), str(interaction.user.id))
    await interaction.response.send_message(
        f'✅ {user.mention} has been unblacklisted.',
        ephemeral=True
    )

@bot.tree.command(name='stats', description='View shop statistics')
@app_commands.default_permissions(administrator=True)
async def stats(interaction: discord.Interaction):
    cursor.execute("SELECT COALESCE(SUM(price), 0) FROM orders WHERE status IN ('paid', 'delivered')")
    revenue = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM orders')
    total_orders = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM orders WHERE status = 'delivered'")
    delivered = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM orders WHERE status = 'pending'")
    pending = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM products WHERE active = 1')
    products = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM users')
    customers = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM tickets WHERE status = 'open'")
    open_tickets = cursor.fetchone()[0]
    
    embed = create_embed(
        '📊 Shop Statistics',
        '',
        'primary',
        [
            {'name': '💰 Total Revenue', 'value': f'{CONFIG["currency_symbol"]}{revenue:.2f}', 'inline': True},
            {'name': '🛒 Total Orders', 'value': str(total_orders), 'inline': True},
            {'name': '✅ Delivered', 'value': str(delivered), 'inline': True},
            {'name': '⏳ Pending', 'value': str(pending), 'inline': True},
            {'name': '📦 Products', 'value': str(products), 'inline': True},
            {'name': '👥 Customers', 'value': str(customers), 'inline': True},
            {'name': '🎫 Open Tickets', 'value': str(open_tickets), 'inline': True},
        ]
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name='deliver', description='Manually deliver an order')
@app_commands.default_permissions(administrator=True)
@app_commands.describe(order='Order ID')
async def deliver(interaction: discord.Interaction, order: str):
    order_id = order.upper()
    success = await deliver_order(order_id)
    await interaction.response.send_message(
        f'✅ Order `{order_id}` delivered.' if success else f'❌ Could not deliver `{order_id}`.',
        ephemeral=True
    )

@bot.tree.command(name='ticketpanel', description='Create ticket panel')
@app_commands.default_permissions(administrator=True)
@app_commands.describe(channel='Channel to send the ticket panel to')
async def ticketpanel(interaction: discord.Interaction, channel: discord.TextChannel = None):
    channel = channel or interaction.channel
    
    embed = create_embed(
        '🛒 Purchase Tickets',
        'Click the button below to create a purchase ticket.\n\n'
        'A support staff will assist you with your purchase.\n'
        'Please provide all required details in the form.',
        'primary',
        footer='All tickets are logged and tracked.'
    )
    
    view = TicketView()
    await channel.send(embed=embed, view=view)
    await interaction.response.send_message(f'✅ Ticket panel sent to {channel.mention}', ephemeral=True)

# ---------- USER COMMANDS ----------

@bot.tree.command(name='shop', description='Browse the shop')
async def shop(interaction: discord.Interaction):
    embed = create_embed(
        '🛍️ Shop',
        'Select a category below.',
        'primary'
    )
    
    view = discord.ui.View()
    for cat in CONFIG['categories']:
        view.add_item(discord.ui.Button(
            label=cat,
            style=discord.ButtonStyle.primary,
            custom_id=f'shop_category_{cat}'
        ))
    
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@bot.tree.command(name='balance', description='Check your balance')
async def balance(interaction: discord.Interaction):
    ensure_user(interaction.user.id)
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (interaction.user.id,))
    user = cursor.fetchone()
    
    embed = create_embed(
        '💰 Your Balance',
        '',
        'success',
        [
            {'name': 'Available', 'value': f'{CONFIG["currency_symbol"]}{user[1]:.2f}', 'inline': True},
            {'name': 'Total Spent', 'value': f'{CONFIG["currency_symbol"]}{user[2]:.2f}', 'inline': True},
            {'name': 'Orders', 'value': str(user[3]), 'inline': True},
        ]
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name='buy', description='Start a purchase')
@app_commands.describe(product='Product name')
async def buy(interaction: discord.Interaction, product: str):
    # Check if user is blacklisted
    cursor.execute('SELECT blacklisted FROM users WHERE user_id = ?', (interaction.user.id,))
    blacklisted = cursor.fetchone()
    if blacklisted and blacklisted[0] == 1:
        await interaction.response.send_message('❌ You are blacklisted from this shop.', ephemeral=True)
        return
    
    cursor.execute('SELECT * FROM products WHERE name LIKE ? AND active = 1', (f'%{product}%',))
    product_data = cursor.fetchone()
    
    if not product_data:
        await interaction.response.send_message('❌ Product not found.', ephemeral=True)
        return
    if product_data[4] < 1:  # stock
        await interaction.response.send_message('❌ Out of stock.', ephemeral=True)
        return
    
    order_id = f"{CONFIG['order_prefix']}{uuid.uuid4().hex[:8].upper()}"
    expires = (datetime.now() + timedelta(minutes=CONFIG['payment_timeout'])).isoformat()
    
    cursor.execute('''
        INSERT INTO orders (id, user_id, product_id, product_name, seller_id, seller_name, price, quantity, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
    ''', (order_id, interaction.user.id, product_data[0], product_data[1], product_data[7], product_data[8], product_data[2], expires))
    conn.commit()
    ensure_user(interaction.user.id)
    log_action('order_created', str(interaction.user.id), data={'order_id': order_id, 'product': product_data[1]})
    
    embed = create_embed(
        '🛒 Order Created',
        f"**Order ID:** `{order_id}`\n"
        f"**Product:** {product_data[1]}\n"
        f"**Price:** {CONFIG['currency_symbol']}{product_data[2]}\n"
        f"**Seller:** {product_data[8] or 'None'}\n\n"
        f"Use `/pay {order_id}` to complete payment",
        'success'
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name='orders', description='View your orders')
async def orders(interaction: discord.Interaction):
    cursor.execute(
        'SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC LIMIT 15',
        (interaction.user.id,)
    )
    orders_data = cursor.fetchall()
    
    if not orders_data:
        await interaction.response.send_message('You have no orders.', ephemeral=True)
        return
    
    embed = create_embed('📜 Your Orders', '', 'primary')
    desc = ''
    for o in orders_data:
        desc += f"**{o[0]}** — {o[3]}\n{CONFIG['currency_symbol']}{o[5]} | `{o[6]}`\n\n"
    embed.description = desc
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name='pay', description='Display payment instructions')
@app_commands.describe(order='Order ID')
async def pay(interaction: discord.Interaction, order: str):
    order_id = order.upper()
    cursor.execute('SELECT * FROM orders WHERE id = ? AND user_id = ?', (order_id, interaction.user.id))
    order_data = cursor.fetchone()
    
    if not order_data:
        await interaction.response.send_message('❌ Order not found.', ephemeral=True)
        return
    if order_data[6] != 'pending':
        await interaction.response.send_message(f'Order is already {order_data[6]}.', ephemeral=True)
        return
    
    embed = create_embed(
        f'💳 Payment Instructions • {order_id}',
        f"**Product:** {order_data[3]}\n"
        f"**Amount:** {CONFIG['currency_symbol']}{order_data[5]}\n"
        f"**Seller:** {order_data[4] or 'None'}\n\n"
        f"**Accepted Methods:**\n"
        f"• Account Balance\n"
        f"• Crypto (ask seller)\n\n"
        f"After payment use `/checkpayment {order_id}`",
        'info'
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name='checkpayment', description='Check payment status')
@app_commands.describe(order='Order ID')
async def checkpayment(interaction: discord.Interaction, order: str):
    order_id = order.upper()
    cursor.execute('SELECT * FROM orders WHERE id = ?', (order_id,))
    order_data = cursor.fetchone()
    
    if not order_data:
        await interaction.response.send_message('❌ Order not found.', ephemeral=True)
        return
    
    if order_data[6] == 'delivered':
        await interaction.response.send_message('✅ Already delivered.', ephemeral=True)
        return
    
    if order_data[6] == 'paid':
        await deliver_order(order_id)
        await interaction.response.send_message('✅ Payment confirmed & delivered.', ephemeral=True)
        return
    
    if interaction.user.guild_permissions.administrator:
        cursor.execute('UPDATE orders SET status = "paid", paid_at = CURRENT_TIMESTAMP WHERE id = ?', (order_id,))
        conn.commit()
        await deliver_order(order_id)
        await interaction.response.send_message(f'✅ Order `{order_id}` marked as paid and delivered.', ephemeral=True)
        return
    
    await interaction.response.send_message(
        f'Current status: **{order_data[6]}**\nWaiting for payment confirmation.',
        ephemeral=True
    )

@bot.tree.command(name='cancel', description='Cancel current pending order')
@app_commands.describe(order='Order ID')
async def cancel(interaction: discord.Interaction, order: str):
    order_id = order.upper()
    cursor.execute('SELECT * FROM orders WHERE id = ? AND user_id = ?', (order_id, interaction.user.id))
    order_data = cursor.fetchone()
    
    if not order_data:
        await interaction.response.send_message('❌ Order not found.', ephemeral=True)
        return
    if order_data[6] != 'pending':
        await interaction.response.send_message('❌ Only pending orders can be cancelled.', ephemeral=True)
        return
    
    cursor.execute('UPDATE orders SET status = "cancelled" WHERE id = ?', (order_id,))
    conn.commit()
    await interaction.response.send_message(f'✅ Order `{order_id}` cancelled.', ephemeral=True)

@bot.tree.command(name='product', description='View product details')
@app_commands.describe(name='Product name')
async def product(interaction: discord.Interaction, name: str):
    cursor.execute('SELECT * FROM products WHERE name LIKE ? AND active = 1', (f'%{name}%',))
    product_data = cursor.fetchone()
    
    if not product_data:
        await interaction.response.send_message('❌ Product not found.', ephemeral=True)
        return
    
    embed = create_embed(
        product_data[1],
        product_data[3] or '*No description*',
        'primary',
        [
            {'name': '💰 Price', 'value': f'{CONFIG["currency_symbol"]}{product_data[2]}', 'inline': True},
            {'name': '📦 Stock', 'value': str(product_data[4]), 'inline': True},
            {'name': '📁 Category', 'value': product_data[5], 'inline': True},
            {'name': '👤 Seller', 'value': product_data[8] or 'None', 'inline': True},
        ]
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name='stock', description='Show current stock')
async def stock(interaction: discord.Interaction):
    cursor.execute('SELECT name, stock, price, category FROM products WHERE active = 1 ORDER BY category, name')
    products = cursor.fetchall()
    
    if not products:
        await interaction.response.send_message('No products available.', ephemeral=True)
        return
    
    embed = create_embed('📦 Current Stock', '', 'info')
    desc = ''
    last_cat = ''
    for p in products:
        if p[3] != last_cat:
            last_cat = p[3]
            desc += f'\n**{last_cat}**\n'
        desc += f"• **{p[0]}** — {CONFIG['currency_symbol']}{p[2]} | Stock: **{p[1]}**\n"
    embed.description = desc[:4090]
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name='ticket', description='Create a purchase ticket')
async def ticket(interaction: discord.Interaction):
    await create_ticket_modal(interaction)

# ---------- SUPPORT COMMANDS ----------

@bot.tree.command(name='support', description='Open a support ticket')
async def support(interaction: discord.Interaction):
    embed = create_embed(
        '🎫 Support',
        'Please click the button below to create a support ticket.\n'
        'A staff member will assist you shortly.',
        'info'
    )
    view = TicketView()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# ============ EVENT HANDLERS ============

@bot.event
async def on_ready():
    print(f'✅ Logged in as {bot.user}')
    print(f'📦 Bot is ready!')
    print(f'🏷️ Bot ID: {bot.user.id}')
    print(f'🔗 Invite: https://discord.com/oauth2/authorize?client_id={bot.user.id}&permissions=8&scope=bot%20applications.commands')
    await bot.change_presence(activity=discord.Game(name='🛒 /shop | 🎫 /ticket'))

@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type == discord.InteractionType.component:
        custom_id = interaction.data.get('custom_id', '')
        
        # Handle create ticket button
        if custom_id == 'create_ticket':
            await create_ticket_modal(interaction)
            return
        
        # Handle close ticket button
        if custom_id == 'close_ticket':
            # Get ticket_id from channel name or database
            ticket_id = None
            cursor.execute('SELECT ticket_id FROM tickets WHERE channel_id = ?', (str(interaction.channel.id),))
            result = cursor.fetchone()
            if result:
                ticket_id = result[0]
            if ticket_id:
                await close_ticket(interaction, ticket_id)
            return
        
        # Handle transcript button
        if custom_id == 'transcript_ticket':
            ticket_id = None
            cursor.execute('SELECT ticket_id FROM tickets WHERE channel_id = ?', (str(interaction.channel.id),))
            result = cursor.fetchone()
            if result:
                ticket_id = result[0]
            if ticket_id:
                await send_transcript(interaction, ticket_id)
            return
        
        # Handle shop category buttons
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
            
            embed = create_embed(
                f'🛍️ {category}',
                'Choose a product from the menu below.',
                'primary'
            )
            await interaction.response.edit_message(embed=embed, view=view)
        
        # Handle product selection
        elif custom_id == 'shop_select_product':
            selected = interaction.data.get('values', [''])[0]
            cursor.execute('SELECT * FROM products WHERE id = ?', (selected,))
            product_data = cursor.fetchone()
            
            if not product_data:
                await interaction.response.send_message('Product not found.', ephemeral=True)
                return
            
            embed = create_embed(
                product_data[1],
                product_data[3] or '*No description*',
                'primary',
                [
                    {'name': '💰 Price', 'value': f'{CONFIG["currency_symbol"]}{product_data[2]}', 'inline': True},
                    {'name': '📦 Stock', 'value': str(product_data[4]), 'inline': True},
                    {'name': '📁 Category', 'value': product_data[5], 'inline': True},
                    {'name': '👤 Seller', 'value': product_data[8] or 'None', 'inline': True},
                ]
            )
            
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
        
        # Handle back button
        elif custom_id == 'shop_back':
            embed = create_embed(
                '🛍️ Shop',
                'Select a category below.',
                'primary'
            )
            
            view = discord.ui.View()
            for cat in CONFIG['categories']:
                view.add_item(discord.ui.Button(
                    label=cat,
                    style=discord.ButtonStyle.primary,
                    custom_id=f'shop_category_{cat}'
                ))
            
            await interaction.response.edit_message(embed=embed, view=view)
        
        # Handle buy now button
        elif custom_id.startswith('buy_now_'):
            product_id = custom_id.replace('buy_now_', '')
            cursor.execute('SELECT * FROM products WHERE id = ? AND active = 1', (product_id,))
            product_data = cursor.fetchone()
            
            if not product_data or product_data[4] < 1:
                await interaction.response.send_message('❌ Out of stock.', ephemeral=True)
                return
            
            # Create order
            order_id = f"{CONFIG['order_prefix']}{uuid.uuid4().hex[:8].upper()}"
            expires = (datetime.now() + timedelta(minutes=CONFIG['payment_timeout'])).isoformat()
            
            cursor.execute('''
                INSERT INTO orders (id, user_id, product_id, product_name, seller_id, seller_name, price, quantity, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            ''', (order_id, interaction.user.id, product_data[0], product_data[1], product_data[7], product_data[8], product_data[2], expires))
            conn.commit()
            ensure_user(interaction.user.id)
            log_action('order_created', str(interaction.user.id), data={'order_id': order_id, 'product': product_data[1]})
            
            embed = create_embed(
                '🛒 Order Created',
                f"**Order ID:** `{order_id}`\n"
                f"**Product:** {product_data[1]}\n"
                f"**Price:** {CONFIG['currency_symbol']}{product_data[2]}\n"
                f"**Seller:** {product_data[8] or 'None'}\n\n"
                f"Click **Pay Now** or use `/pay {order_id}`",
                'success'
            )
            
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
        
        # Handle pay button
        elif custom_id.startswith('pay_btn_'):
            order_id = custom_id.replace('pay_btn_', '')
            cursor.execute('SELECT * FROM orders WHERE id = ? AND user_id = ?', (order_id, interaction.user.id))
            order_data = cursor.fetchone()
            
            if not order_data:
                await interaction.response.send_message('Order not found.', ephemeral=True)
                return
            if order_data[6] != 'pending':
                await interaction.response.send_message(f'Order is already {order_data[6]}.', ephemeral=True)
                return
            
            embed = create_embed(
                f'💳 Payment • {order_id}',
                f"**Product:** {order_data[3]}\n"
                f"**Amount due:** {CONFIG['currency_symbol']}{order_data[5]}\n"
                f"**Seller:** {order_data[4] or 'None'}\n\n"
                f"**Payment Methods:**\n"
                f"• Account Balance\n"
                f"• Crypto (ask seller)\n\n"
                f"After payment use `/checkpayment {order_id}`",
                'info'
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        
        # Handle cancel order button
        elif custom_id.startswith('cancel_order_'):
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

# ============ RUN BOT ============
if __name__ == '__main__':
    if not TOKEN:
        print("❌ Error: BOT_TOKEN environment variable is not set!")
        print("Please set BOT_TOKEN in your .env file or Railway environment variables.")
        exit(1)
    
    print("🚀 Starting RoStock Discord Bot...")
    print(f"📊 Database: {DB_PATH}")
    bot.run(TOKEN)
