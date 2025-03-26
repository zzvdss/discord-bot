import discord
from discord.ext import commands
from discord.ui import Select, View, Button
import os
import json
import time
import asyncio
from collections import defaultdict
from datetime import timedelta

# Constants
POINTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_points.json")
REDEEM_COOLDOWN = 900  # 15 minutes
CLAIM_COOLDOWN = 60  # 5 minutes
CLAIM_POINTS = 50
LEADERBOARD_LIMIT = 10
TIMEOUT_BASE_COST = 1000  # Base cost for 3 minutes
TIMEOUT_BASE_DURATION = 3  # Base duration in minutes
REDEEM_TIMEOUT = 300  # 5 minutes for redeem message to disappear
ADMIN_USER_ID = 776744923738800129  # Your user ID

# Data storage
user_points = defaultdict(int)
redeem_cooldowns = {}

def save_points():
    """Save points data to file"""
    try:
        with open(POINTS_FILE, "w") as f:
            json.dump(dict(user_points), f, indent=4)
        return True
    except Exception as e:
        print(f"Error saving points: {e}")
        return False

def load_points():
    """Load points data from file"""
    global user_points
    try:
        if os.path.exists(POINTS_FILE):
            with open(POINTS_FILE, "r") as f:
                user_points = defaultdict(int, {int(k): v for k, v in json.load(f).items()})
        else:
            user_points = defaultdict(int)
    except Exception as e:
        print(f"Error loading points: {e}")
        user_points = defaultdict(int)

class UserSelect(discord.ui.UserSelect):
    def __init__(self, placeholder="Select user..."):
        super().__init__(placeholder=placeholder, min_values=1, max_values=1)
        
    async def callback(self, interaction: discord.Interaction):
        self.view.target = self.values[0]
        await interaction.response.defer()

class ChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, placeholder="Select channel..."):
        super().__init__(
            placeholder=placeholder, 
            channel_types=[discord.ChannelType.voice], 
            min_values=1, 
            max_values=1
        )
        
    async def callback(self, interaction: discord.Interaction):
        self.view.channel = self.values[0]
        await interaction.response.defer()

class PointSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.log_channel_ids = [1351561404150448248, 1350543441821564988]
        load_points()

    async def log_activity(self, message):
        """Log activity to designated channels"""
        for channel_id in self.log_channel_ids:
            channel = self.bot.get_channel(channel_id)
            if channel:
                try:
                    await channel.send(message)
                except discord.errors.HTTPException as e:
                    print(f"Error sending log message: {e}")

    def is_admin(self, user):
        """Check if user is admin via ID only"""
        return user.id == ADMIN_USER_ID

    @commands.command()
    async def leaderboard(self, ctx):
        """Show points leaderboard"""
        sorted_users = sorted(
            user_points.items(), 
            key=lambda x: x[1], 
            reverse=True
        )[:LEADERBOARD_LIMIT]
        
        embed = discord.Embed(
            title="🏆 Points Leaderboard",
            color=discord.Color.gold()
        )
        
        for idx, (user_id, points) in enumerate(sorted_users, 1):
            user = self.bot.get_user(user_id)
            username = user.name if user else f"Unknown User ({user_id})"
            embed.add_field(
                name=f"{idx}. {username}",
                value=f"`{points}` points",
                inline=False
            )
        
        embed.set_footer(text=f"Your points: {user_points.get(ctx.author.id, 0)}")
        await ctx.send(embed=embed)

    @commands.command()
    async def givepoints(self, ctx):
        """Admin command to give points (with dropdown)"""
        if not self.is_admin(ctx.author):
            await ctx.send("❌ Hanya owner bot yang bisa menggunakan command ini!", ephemeral=True)
            return

        class GivePointsView(View):
            def __init__(self, author):
                super().__init__(timeout=60)
                self.author = author
                self.add_item(UserSelect())
                self.points = 0
                self.target = None
                self.message = None

            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                if interaction.user != self.author:
                    await interaction.response.send_message("❌ Ini bukan interaksi kamu!", ephemeral=True)
                    return False
                return True

            @discord.ui.button(label="Set Points", style=discord.ButtonStyle.primary)
            async def set_points(self, interaction: discord.Interaction, button: discord.ui.Button):
                await interaction.response.send_modal(PointsModal(self))

            @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success, disabled=True)
            async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
                if self.points <= 0:
                    await interaction.response.send_message("❌ Poin harus positif!", ephemeral=True)
                    return

                user_points[self.target.id] += self.points
                save_points()

                await interaction.response.send_message(
                    f"✅ {self.points} poin diberikan ke {self.target.mention}! "
                    f"Total: {user_points[self.target.id]}",
                    ephemeral=True
                )
                await self.cog.log_activity(
                    f"🎁 {self.author.mention} memberikan {self.points} poin ke {self.target.mention}!"
                )
                self.stop()

                if self.message:
                    try:
                        await self.message.delete()
                    except:
                        pass

            async def on_timeout(self):
                if self.message:
                    try:
                        await self.message.delete()
                    except:
                        pass

        class PointsModal(discord.ui.Modal):
            def __init__(self, view):
                super().__init__(title="Set Jumlah Poin")
                self.view = view
                self.points_input = discord.ui.TextInput(
                    label="Jumlah Poin",
                    placeholder="Masukkan jumlah poin...",
                    min_length=1,
                    max_length=10
                )
                self.add_item(self.points_input)

            async def on_submit(self, interaction: discord.Interaction):
                try:
                    points = int(self.points_input.value)
                    if points <= 0:
                        await interaction.response.send_message("❌ Poin harus positif!", ephemeral=True)
                        return

                    self.view.points = points
                    self.view.confirm.disabled = False
                    await interaction.response.edit_message(view=self.view)
                except ValueError:
                    await interaction.response.send_message("❌ Masukkan angka yang valid!", ephemeral=True)

        view = GivePointsView(ctx.author)
        view.cog = self
        message = await ctx.send("**Give Points**\nPilih user dan set jumlah poin:", view=view, ephemeral=True)
        view.message = message

    @commands.command()
    async def removepoints(self, ctx):
        """Admin command to remove points (with dropdown)"""
        if not self.is_admin(ctx.author):
            await ctx.send("❌ Hanya owner bot yang bisa menggunakan command ini!", ephemeral=True)
            return

        class RemovePointsView(View):
            def __init__(self, author):
                super().__init__(timeout=60)
                self.author = author
                self.add_item(UserSelect())
                self.points = 0
                self.target = None
                self.message = None

            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                if interaction.user != self.author:
                    await interaction.response.send_message("❌ Ini bukan interaksi kamu!", ephemeral=True)
                    return False
                return True

            @discord.ui.button(label="Set Points", style=discord.ButtonStyle.primary)
            async def set_points(self, interaction: discord.Interaction, button: discord.ui.Button):
                await interaction.response.send_modal(PointsModal(self))

            @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success, disabled=True)
            async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
                if self.points <= 0:
                    await interaction.response.send_message("❌ Poin harus positif!", ephemeral=True)
                    return

                if user_points.get(self.target.id, 0) < self.points:
                    await interaction.response.send_message(
                        f"❌ {self.target.mention} hanya memiliki {user_points.get(self.target.id, 0)} poin!",
                        ephemeral=True
                    )
                    return

                user_points[self.target.id] -= self.points
                save_points()

                await interaction.response.send_message(
                    f"✅ {self.points} poin dihapus dari {self.target.mention}! "
                    f"Sisa: {user_points[self.target.id]}",
                    ephemeral=True
                )
                await self.cog.log_activity(
                    f"❌ {self.author.mention} menghapus {self.points} poin dari {self.target.mention}!"
                )
                self.stop()

                if self.message:
                    try:
                        await self.message.delete()
                    except:
                        pass

            async def on_timeout(self):
                if self.message:
                    try:
                        await self.message.delete()
                    except:
                        pass

        class PointsModal(discord.ui.Modal):
            def __init__(self, view):
                super().__init__(title="Set Jumlah Poin")
                self.view = view
                self.points_input = discord.ui.TextInput(
                    label="Jumlah Poin",
                    placeholder="Masukkan jumlah poin...",
                    min_length=1,
                    max_length=10
                )
                self.add_item(self.points_input)

            async def on_submit(self, interaction: discord.Interaction):
                try:
                    points = int(self.points_input.value)
                    if points <= 0:
                        await interaction.response.send_message("❌ Poin harus positif!", ephemeral=True)
                        return

                    self.view.points = points
                    self.view.confirm.disabled = False
                    await interaction.response.edit_message(view=self.view)
                except ValueError:
                    await interaction.response.send_message("❌ Masukkan angka yang valid!", ephemeral=True)

        view = RemovePointsView(ctx.author)
        view.cog = self
        message = await ctx.send("**Remove Points**\nPilih user dan set jumlah poin:", view=view, ephemeral=True)
        view.message = message

    @commands.command()
    async def redeem(self, ctx):
        """Redeem points for actions"""
        user_id = ctx.author.id
        current_time = time.time()

        if user_id in redeem_cooldowns and current_time - redeem_cooldowns[user_id] < REDEEM_COOLDOWN:
            remaining = REDEEM_COOLDOWN - (current_time - redeem_cooldowns[user_id])
            await ctx.send(f"❌ Please wait {remaining/60:.1f} minutes before redeeming again!", ephemeral=True)
            return

        class RedeemView(View):
            def __init__(self, author):
                super().__init__(timeout=REDEEM_TIMEOUT)
                self.author = author
                self.action = None
                self.target = None
                self.channel = None
                self.duration = None
                self.message = None
                self.success = False

            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                if interaction.user != self.author:
                    await interaction.response.send_message("❌ This is not your interaction!", ephemeral=True)
                    return False
                return True

            async def on_timeout(self):
                if not self.success and self.message:
                    try:
                        await self.message.delete()
                    except:
                        pass

            @discord.ui.select(
                placeholder="Select action...",
                options=[
                    discord.SelectOption(label="Timeout", value="timeout", description="1000 points per 3 mins", emoji="⏳"),
                    discord.SelectOption(label="Move VC", value="move", description="300 points", emoji="🚚"),
                    discord.SelectOption(label="Kick VC", value="kick", description="300 points", emoji="🚪"),
                    discord.SelectOption(label="Kick Lock VC", value="kick_lock", description="5000 points", emoji="🔒")
                ]
            )
            async def select_action(self, interaction: discord.Interaction, select: discord.ui.Select):
                self.action = select.values[0]
                await interaction.response.defer()
                self.clear_items()
                self.add_item(UserSelect())
                
                if self.action == "timeout":
                    self.duration_button = Button(label="Set Duration", style=discord.ButtonStyle.primary)
                    
                    async def duration_callback(interaction: discord.Interaction):
                        await interaction.response.send_modal(DurationModal(self))
                    
                    self.duration_button.callback = duration_callback
                    self.add_item(self.duration_button)
                elif self.action == "move":
                    self.add_item(ChannelSelect())
                
                self.confirm_button = Button(label="Confirm", style=discord.ButtonStyle.success)
                
                async def confirm_callback(interaction: discord.Interaction):
                    if not self.target:
                        await interaction.response.send_message("❌ Please select a user!", ephemeral=True)
                        return
                    
                    if self.action == "timeout":
                        cost = (self.duration // TIMEOUT_BASE_DURATION) * TIMEOUT_BASE_COST
                        if self.duration % TIMEOUT_BASE_DURATION != 0:
                            cost += TIMEOUT_BASE_COST
                    else:
                        costs = {
                            "move": 300,
                            "kick": 300,
                            "kick_lock": 5000
                        }
                        cost = costs.get(self.action, 0)
                    
                    if user_points[user_id] < cost:
                        await interaction.response.send_message(
                            f"❌ You need {cost} points! You have {user_points[user_id]}",
                            ephemeral=True
                        )
                        return
                    
                    try:
                        if self.action == "timeout":
                            await self.target.timeout(discord.utils.utcnow() + timedelta(minutes=self.duration))
                            msg = f"⏳ {self.target.mention} timed out for {self.duration} minutes (Cost: {cost} points)"
                        elif self.action == "move":
                            await self.target.move_to(self.channel)
                            msg = f"🚚 Moved {self.target.mention} to {self.channel.name} (Cost: 300 points)"
                        elif self.action == "kick":
                            await self.target.move_to(None)
                            msg = f"🚪 Kicked {self.target.mention} from VC (Cost: 300 points)"
                        elif self.action == "kick_lock":
                            await self.target.voice.channel.set_permissions(self.target, connect=False)
                            await self.target.move_to(None)
                            msg = f"🔒 Kicked & locked {self.target.mention} from VC (Cost: 5000 points)"
                        
                        user_points[user_id] -= cost
                        redeem_cooldowns[user_id] = current_time
                        save_points()
                        
                        self.success = True
                        
                        # Delete the original message
                        if self.message:
                            try:
                                await self.message.delete()
                            except:
                                pass
                        
                        # Send success message
                        success_embed = discord.Embed(
                            description=f"✅ {msg} by {self.author.mention}",
                            color=discord.Color.green()
                        )
                        await interaction.response.send_message(embed=success_embed, ephemeral=True)
                        
                        await self.cog.log_activity(f"{msg} by {self.author.mention}")
                        self.stop()
                    except Exception as e:
                        await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)
                
                self.confirm_button.callback = confirm_callback
                self.add_item(self.confirm_button)
                await interaction.edit_original_response(view=self)

        class DurationModal(discord.ui.Modal):
            def __init__(self, view):
                super().__init__(title="Set Timeout Duration")
                self.view = view
                self.duration_input = discord.ui.TextInput(
                    label="Duration (minutes)",
                    placeholder=f"Enter timeout duration (multiples of {TIMEOUT_BASE_DURATION} minutes)...",
                    default=str(TIMEOUT_BASE_DURATION),
                    min_length=1,
                    max_length=3
                )
                self.add_item(self.duration_input)

            async def on_submit(self, interaction: discord.Interaction):
                try:
                    duration = int(self.duration_input.value)
                    if duration <= 0:
                        await interaction.response.send_message("❌ Duration must be positive!", ephemeral=True)
                        return
                    
                    cost = (duration // TIMEOUT_BASE_DURATION) * TIMEOUT_BASE_COST
                    if duration % TIMEOUT_BASE_DURATION != 0:
                        cost += TIMEOUT_BASE_COST
                    
                    self.view.duration = duration
                    
                    await interaction.response.send_message(
                        f"ℹ️ Timeout for {duration} minutes will cost {cost} points (1000 per 3 minutes). "
                        "Click Confirm to proceed.",
                        ephemeral=True
                    )
                except ValueError:
                    await interaction.response.send_message("❌ Please enter a valid number!", ephemeral=True)

        view = RedeemView(ctx.author)
        view.cog = self
        message = await ctx.send("**Redeem Points**\nSelect action:", view=view, ephemeral=True)
        view.message = message

    @commands.command()
    async def claim(self, ctx):
        """Claim free points"""
        user_id = ctx.author.id
        current_time = time.time()

        if user_id in redeem_cooldowns and current_time - redeem_cooldowns[user_id] < CLAIM_COOLDOWN:
            remaining = CLAIM_COOLDOWN - (current_time - redeem_cooldowns[user_id])
            await ctx.send(f"❌ Please wait {remaining/60:.1f} minutes before claiming again!", ephemeral=True)
            return

        user_points[user_id] += CLAIM_POINTS
        redeem_cooldowns[user_id] = current_time
        save_points()

        await ctx.send(
            f"✅ {ctx.author.mention} claimed {CLAIM_POINTS} points! "
            f"Total: {user_points[user_id]}",
            ephemeral=True
        )
        await self.log_activity(f"📥 {ctx.author.mention} claimed {CLAIM_POINTS} points")

    @commands.command()
    async def points(self, ctx, user: discord.Member = None):
        """Check your points"""
        target = user or ctx.author
        await ctx.send(f"💰 {target.mention} has {user_points.get(target.id, 0)} points!", ephemeral=True)

    @commands.command()
    async def poininfo(self, ctx):
        """Display information about the point system"""
        embed = discord.Embed(
            title="📢 **Sistem Poin & Redeem** 🎯",
            color=discord.Color.gold()
        )
        
        embed.add_field(
            name="🔹 **Claim Poin**",
            value=(
                "Setiap pengguna dapat memperoleh 50 poin setiap 1 menit\n"
                "```!claim```\n"
                "Poin akan otomatis tersimpan di akun Anda"
            ),
            inline=False
        )
        
        embed.add_field(
            name="🔹 **Redeem Poin**",
            value=(
                "Gunakan ```!redeem``` untuk membuka menu redeem interaktif\n\n"
                "**Pilihan Aksi:**\n"
                f"⏳ **Timeout** - {TIMEOUT_BASE_COST} poin per {TIMEOUT_BASE_DURATION} menit\n"
                "> Memberikan timeout (tidak bisa chat/voice)\n\n"
                "🚚 **Pindahkan VC** - 300 poin\n"
                "> Memindahkan user ke voice channel pilihan\n\n"
                "🚪 **Kick VC** - 300 poin\n"
                "> Mengeluarkan user dari voice channel\n\n"
                "🔒 **Kick & Lock VC** - 5000 poin\n"
                "> Mengeluarkan dan memblokir akses voice channel"
            ),
            inline=False
        )
        
        embed.add_field(
            name="⚠️ **Peraturan**",
            value=(
                "• Cooldown 15 menit setelah redeem\n"
                "• Hanya bisa digunakan pada online members\n"
                "• Poin tidak bisa ditransfer ke user lain\n"
                "• Pesan redeem akan hilang setelah 5 menit jika tidak digunakan"
            ),
            inline=False
        )
        
        embed.set_footer(text="Gunakan poin dengan bijak!")
        
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(PointSystem(bot))