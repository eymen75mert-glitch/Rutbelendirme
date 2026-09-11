# -*- coding: utf-8 -*-
"""
ROBLOX GRUP RUTBELENDIRME DISCORD BOTU
========================================
Tek dosyalik, Turkce komutlu, Roblox grup rutbe yonetim botu.

OZELLIKLER
----------
- /grup-bagla        -> Discord sunucusunu bir Roblox Grup ID'sine baglar (Yonetici)
- /bot-hesap-bagla    -> Botun rutbe verme islemlerini yapacagi Roblox hesabinin
                         .ROBLOSECURITY cookie'sini baglar (Yonetici)
- /dogrula            -> Kullanicinin Roblox hesabini "About/Hakkinda" alanina
                         yazdirilacak bir kodla dogrulamasini baslatir
- /dogrula-tamamla    -> Dogrulamayi tamamlar (kod About kismina yazildiktan sonra)
- /rutbe-degistir     -> Belirtilen uyeye, ismiyle belirtilen rutbeyi verir
- /terfi              -> Belirtilen uyeyi bir ust rutbeye cikarir
- /tenzil             -> Belirtilen uyeyi bir alt rutbeye indirir
- /ayarlar            -> Sunucunun mevcut bot ayarlarini gosterir
- /dogrulama-durumu   -> Kendi Roblox dogrulama durumunuzu gosterir

YETKI MANTIGI
-------------
Bir kullanici baskasinin rutbesini degistirmek istediginde:
  1) Komutu kullanan kisi Roblox hesabini dogrulamis olmali.
  2) Komutu kullanan kisinin Roblox grubundaki rutbesi, islem yapilacak
     kisinin MEVCUT rutbesinden yuksek olmali (sadece kendinden alt olanlari
     duzenleyebilir).
  3) Komutu kullanan kisi, kendi rutbesinden DUSUK bir rutbe verebilir
     (kendi rutbesiyle ayni veya daha yuksek rutbe VEREMEZ).
  4) Botun kullandigi Roblox hesabinin rutbesi de hem mevcut hem hedef
     rutbeden yuksek olmali (Roblox'un kendi kurali - aksi halde API zaten
     reddeder).

KURULUM
-------
1) pip install -U discord.py requests
2) Asagida DISCORD_TOKEN ortam degiskenini ayarlayin ya da dogrudan
   TOKEN degiskenine yapistirin:
       Windows (PowerShell): $env:DISCORD_TOKEN="TOKEN_BURAYA"
       Linux/Mac:             export DISCORD_TOKEN="TOKEN_BURAYA"
3) python rutbe_bot.py
4) Botu sunucunuza "applications.commands" ve "bot" yetkileriyle davet edin.
5) Sunucuda /grup-bagla ve /bot-hesap-bagla komutlarini calistirin.

GUVENLIK UYARISI
-----------------
- .ROBLOSECURITY cookie'si botun Roblox hesabina TAM erisim saglar.
  * Bu cookie'yi SADECE botun kendisi icin ayrilmis, grupta rutbe verme
    yetkisi olan (ama sahip/owner OLMAYAN) ayri bir hesapla kullanin.
  * /bot-hesap-bagla komutunu mutlaka ozel/yetkili-only bir kanalda
    calistirin; slash komut mesaji kanaldaki herkese gorunur olabilir.
    Komut sonrasi mesaji elinizden geldigince hizli silin.
  * Cookie'yi kimseyle paylasmayin, duzenli araliklarla yenileyin.
- config.json dosyasi hassas veriler icerir (cookie dahil). Bu dosyayi
  asla paylasmayin / GitHub'a yuklemeyin.
"""

import os
import json
import random
import string
import asyncio

import requests
import discord
from discord import app_commands
from discord.ext import commands

# ---------------------------------------------------------------------------
# AYARLAR
# ---------------------------------------------------------------------------

TOKEN = os.getenv("DISCORD_TOKEN", "BURAYA_DISCORD_BOT_TOKENINIZI_YAZIN")
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

ROBLOX_HEADERS = {"User-Agent": "Mozilla/5.0 (RutbeBot)"}


# ---------------------------------------------------------------------------
# CONFIG YONETIMI (config.json)
# ---------------------------------------------------------------------------

def config_yukle():
    if not os.path.exists(CONFIG_PATH):
        return {"guilds": {}, "verifications": {}}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {"guilds": {}, "verifications": {}}


def config_kaydet(data):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def guild_ayarlari(data, guild_id: int):
    gid = str(guild_id)
    if gid not in data["guilds"]:
        data["guilds"][gid] = {"group_id": None, "cookie": None}
    return data["guilds"][gid]


# ---------------------------------------------------------------------------
# ROBLOX API YARDIMCISI
# ---------------------------------------------------------------------------

class RobloxAPIHatasi(Exception):
    pass


class RobloxAPI:
    """Roblox web API'siyle konusan kucuk bir istemci.
    Okuma (public) istekleri cookie olmadan da calisir.
    Rutbe DEGISTIRME (PATCH) istekleri icin gecerli bir cookie sarttir.
    """

    def __init__(self, cookie: str | None = None):
        self.session = requests.Session()
        self.session.headers.update(ROBLOX_HEADERS)
        if cookie:
            self.session.cookies.set(".ROBLOSECURITY", cookie, domain=".roblox.com")
        self.csrf_token = None

    def _istek(self, method, url, **kwargs):
        headers = kwargs.pop("headers", {})
        if self.csrf_token:
            headers["X-CSRF-TOKEN"] = self.csrf_token
        resp = self.session.request(method, url, headers=headers, timeout=15, **kwargs)
        if resp.status_code == 403 and "x-csrf-token" in resp.headers:
            self.csrf_token = resp.headers["x-csrf-token"]
            headers["X-CSRF-TOKEN"] = self.csrf_token
            resp = self.session.request(method, url, headers=headers, timeout=15, **kwargs)
        return resp

    def dogrulanmis_hesap(self):
        """Cookie'nin ait oldugu Roblox hesabini dogrular."""
        r = self.session.get("https://users.roblox.com/v1/users/authenticated", timeout=15)
        if r.status_code != 200:
            return None
        return r.json()

    def kullanici_id_bul(self, username: str):
        r = self.session.post(
            "https://users.roblox.com/v1/usernames/users",
            json={"usernames": [username], "excludeBannedUsers": False},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        veri = r.json().get("data", [])
        return veri[0]["id"] if veri else None

    def kullanici_bilgisi(self, user_id: int):
        r = self.session.get(f"https://users.roblox.com/v1/users/{user_id}", timeout=15)
        if r.status_code != 200:
            return None
        return r.json()

    def grup_rolleri(self, group_id: int):
        r = self.session.get(f"https://groups.roblox.com/v1/groups/{group_id}/roles", timeout=15)
        if r.status_code != 200:
            raise RobloxAPIHatasi("Grup bulunamadi ya da Roblox API'ye ulasilamadi.")
        roller = r.json().get("roles", [])
        return sorted(roller, key=lambda x: x["rank"])

    def kullanicinin_grup_rutbesi(self, user_id: int, group_id: int):
        r = self.session.get(
            f"https://groups.roblox.com/v1/users/{user_id}/groups/roles", timeout=15
        )
        if r.status_code != 200:
            return None
        for g in r.json().get("data", []):
            if g["group"]["id"] == group_id:
                return g["role"]  # {id, name, rank}
        return None

    def rutbe_ayarla(self, group_id: int, user_id: int, role_id: int):
        url = f"https://groups.roblox.com/v1/groups/{group_id}/users/{user_id}"
        r = self._istek("PATCH", url, json={"roleId": role_id})
        return r


# ---------------------------------------------------------------------------
# YETKI KONTROL FONKSIYONU
# ---------------------------------------------------------------------------

def yetki_kontrol(davetci_rank: int, hedef_mevcut_rank: int, hedef_yeni_rank: int, bot_rank: int):
    """Rutbe islemi yapilabilir mi kontrol eder. (izin_var, hata_mesaji)"""
    if davetci_rank <= hedef_mevcut_rank:
        return False, "Bu kisi sizinle ayni veya sizden ust rutbede oldugu icin islem yapamazsiniz."
    if davetci_rank <= hedef_yeni_rank:
        return False, "Kendi rutbenizle ayni veya daha ust bir rutbe veremezsiniz."
    if bot_rank <= hedef_mevcut_rank or bot_rank <= hedef_yeni_rank:
        return False, "Botun Roblox hesabinin rutbesi bu islemi yapmaya yetmiyor (bot hesabi daha yuksek rutbede olmali)."
    return True, None


# ---------------------------------------------------------------------------
# DISCORD BOT KURULUMU
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    try:
        await bot.tree.sync()
    except Exception as e:
        print(f"Komut senkronizasyon hatasi: {e}")
    print(f"Giris yapildi: {bot.user} (ID: {bot.user.id})")


# ---------------------------------------------------------------------------
# /grup-bagla
# ---------------------------------------------------------------------------

@bot.tree.command(name="grup-bagla", description="Bu Discord sunucusunu bir Roblox grubuna baglar.")
@app_commands.describe(grup_id="Roblox Grup ID numarasi")
@app_commands.checks.has_permissions(administrator=True)
async def grup_bagla(interaction: discord.Interaction, grup_id: str):
    await interaction.response.defer(ephemeral=True)
    if not grup_id.isdigit():
        await interaction.followup.send("Grup ID sadece sayilardan olusmali.", ephemeral=True)
        return

    api = RobloxAPI()
    try:
        roller = api.grup_rolleri(int(grup_id))
    except RobloxAPIHatasi as e:
        await interaction.followup.send(f"Hata: {e}", ephemeral=True)
        return

    data = config_yukle()
    ayar = guild_ayarlari(data, interaction.guild_id)
    ayar["group_id"] = int(grup_id)
    config_kaydet(data)

    rol_listesi = ", ".join(f"{r['name']} ({r['rank']})" for r in roller)
    await interaction.followup.send(
        f"Grup basariyla baglandi: **{grup_id}**\nMevcut rutbeler: {rol_listesi}",
        ephemeral=True,
    )


# ---------------------------------------------------------------------------
# /bot-hesap-bagla
# ---------------------------------------------------------------------------

@bot.tree.command(
    name="bot-hesap-bagla",
    description="Botun rutbe vermek icin kullanacagi Roblox hesabinin cookie'sini baglar.",
)
@app_commands.describe(cookie="Botun Roblox hesabinin .ROBLOSECURITY cookie degeri")
@app_commands.checks.has_permissions(administrator=True)
async def bot_hesap_bagla(interaction: discord.Interaction, cookie: str):
    await interaction.response.defer(ephemeral=True)

    data = config_yukle()
    ayar = guild_ayarlari(data, interaction.guild_id)
    if not ayar.get("group_id"):
        await interaction.followup.send(
            "Once /grup-bagla komutuyla bir Roblox grubu baglamalisiniz.", ephemeral=True
        )
        return

    api = RobloxAPI(cookie)
    hesap = api.dogrulanmis_hesap()
    if not hesap:
        await interaction.followup.send(
            "Cookie gecersiz veya suresi dolmus. Roblox'a giris yapip yeni cookie alin.",
            ephemeral=True,
        )
        return

    rutbe = api.kullanicinin_grup_rutbesi(hesap["id"], ayar["group_id"])
    if not rutbe:
        await interaction.followup.send(
            f"Bu hesap ({hesap['name']}) hedef Roblox grubunun uyesi degil.", ephemeral=True
        )
        return

    ayar["cookie"] = cookie
    config_kaydet(data)

    await interaction.followup.send(
        f"Bot hesabi baglandi: **{hesap['name']}** (Grup rutbesi: {rutbe['name']} / {rutbe['rank']})\n"
        f"Onemli: Bu mesaji ve komut gecmisini guvenlik icin temizlemeyi unutmayin.",
        ephemeral=True,
    )


# ---------------------------------------------------------------------------
# /dogrula ve /dogrula-tamamla  (Roblox hesap dogrulama - About/Hakkinda ile)
# ---------------------------------------------------------------------------

def kod_uret():
    return "DOGRULA-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


@bot.tree.command(name="dogrula", description="Roblox hesabinizi dogrulamaya baslatir.")
@app_commands.describe(kullanici_adi="Roblox kullanici adiniz")
async def dogrula(interaction: discord.Interaction, kullanici_adi: str):
    await interaction.response.defer(ephemeral=True)

    api = RobloxAPI()
    roblox_id = api.kullanici_id_bul(kullanici_adi)
    if not roblox_id:
        await interaction.followup.send("Bu kullanici adinda bir Roblox hesabi bulunamadi.", ephemeral=True)
        return

    kod = kod_uret()
    data = config_yukle()
    data["verifications"][str(interaction.user.id)] = {
        "roblox_id": roblox_id,
        "username": kullanici_adi,
        "kod": kod,
        "onaylandi": False,
    }
    config_kaydet(data)

    await interaction.followup.send(
        f"Dogrulama baslatildi.\n"
        f"1) Roblox profilinize gidin: https://www.roblox.com/users/{roblox_id}/profile\n"
        f"2) **Hakkinda (About)** kismina su kodu yazip kaydedin:\n```\n{kod}\n```\n"
        f"3) Kodu ekledikten sonra `/dogrula-tamamla` komutunu calistirin.\n"
        f"(Dogrulama tamamlandiktan sonra kodu About kisminda birakabilirsiniz.)",
        ephemeral=True,
    )


@bot.tree.command(name="dogrula-tamamla", description="Roblox hesap dogrulamanizi tamamlar.")
async def dogrula_tamamla(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    data = config_yukle()
    kayit = data["verifications"].get(str(interaction.user.id))
    if not kayit:
        await interaction.followup.send("Once /dogrula komutuyla dogrulama baslatmalisiniz.", ephemeral=True)
        return

    api = RobloxAPI()
    bilgi = api.kullanici_bilgisi(kayit["roblox_id"])
    if not bilgi:
        await interaction.followup.send("Roblox hesap bilgisi alinamadi, tekrar deneyin.", ephemeral=True)
        return

    aciklama = bilgi.get("description", "") or ""
    if kayit["kod"] not in aciklama:
        await interaction.followup.send(
            "Kod, Roblox 'Hakkinda' bolumunde bulunamadi. Kaydettiginizden emin olun ve tekrar deneyin.",
            ephemeral=True,
        )
        return

    kayit["onaylandi"] = True
    config_kaydet(data)

    await interaction.followup.send(
        f"Dogrulama basarili! Discord hesabiniz Roblox kullanicisi **{kayit['username']}** ile eslendi.",
        ephemeral=True,
    )


@bot.tree.command(name="dogrulama-durumu", description="Roblox dogrulama durumunuzu gosterir.")
async def dogrulama_durumu(interaction: discord.Interaction):
    data = config_yukle()
    kayit = data["verifications"].get(str(interaction.user.id))
    if not kayit or not kayit.get("onaylandi"):
        await interaction.response.send_message("Roblox hesabiniz henuz dogrulanmadi. /dogrula ile baslayin.", ephemeral=True)
        return
    await interaction.response.send_message(
        f"Dogrulanmis Roblox hesabiniz: **{kayit['username']}**", ephemeral=True
    )


# ---------------------------------------------------------------------------
# /ayarlar
# ---------------------------------------------------------------------------

@bot.tree.command(name="ayarlar", description="Sunucunun bot ayarlarini gosterir.")
@app_commands.checks.has_permissions(administrator=True)
async def ayarlar(interaction: discord.Interaction):
    data = config_yukle()
    ayar = guild_ayarlari(data, interaction.guild_id)
    config_kaydet(data)

    grup_id = ayar.get("group_id")
    cookie_var = "Evet" if ayar.get("cookie") else "Hayir"

    bot_hesap_adi = "-"
    if ayar.get("cookie"):
        api = RobloxAPI(ayar["cookie"])
        hesap = api.dogrulanmis_hesap()
        if hesap:
            bot_hesap_adi = hesap["name"]

    await interaction.response.send_message(
        f"**Grup ID:** {grup_id or 'Baglanmadi'}\n"
        f"**Bot Roblox Hesabi Baglandi mi:** {cookie_var}\n"
        f"**Bot Roblox Hesabi:** {bot_hesap_adi}",
        ephemeral=True,
    )


# ---------------------------------------------------------------------------
# YARDIMCI: rutbe islemi icin gerekli tum kontrolleri yapan ortak fonksiyon
# ---------------------------------------------------------------------------

async def rutbe_islemi_hazirla(interaction: discord.Interaction, hedef_discord: discord.Member):
    """Ortak on kontrolleri yapar. Basarisizsa None, basariliysa
    (data, ayar, api, group_id, davetci_rutbe, hedef_rutbe, hedef_roblox_id, roller) doner.
    """
    data = config_yukle()
    ayar = guild_ayarlari(data, interaction.guild_id)

    if not ayar.get("group_id") or not ayar.get("cookie"):
        await interaction.followup.send(
            "Bu sunucuda once /grup-bagla ve /bot-hesap-bagla komutlari calistirilmali.",
            ephemeral=True,
        )
        return None

    davetci_kayit = data["verifications"].get(str(interaction.user.id))
    if not davetci_kayit or not davetci_kayit.get("onaylandi"):
        await interaction.followup.send("Once /dogrula ile Roblox hesabinizi dogrulamalisiniz.", ephemeral=True)
        return None

    hedef_kayit = data["verifications"].get(str(hedef_discord.id))
    if not hedef_kayit or not hedef_kayit.get("onaylandi"):
        await interaction.followup.send(
            f"{hedef_discord.mention} henuz Roblox hesabini dogrulamamis.", ephemeral=True
        )
        return None

    api = RobloxAPI(ayar["cookie"])
    group_id = ayar["group_id"]

    bot_hesap = api.dogrulanmis_hesap()
    if not bot_hesap:
        await interaction.followup.send("Bot Roblox hesabinin cookie'si gecersiz, /bot-hesap-bagla ile yenileyin.", ephemeral=True)
        return None

    bot_rutbe = api.kullanicinin_grup_rutbesi(bot_hesap["id"], group_id)
    davetci_rutbe = api.kullanicinin_grup_rutbesi(davetci_kayit["roblox_id"], group_id)
    hedef_rutbe = api.kullanicinin_grup_rutbesi(hedef_kayit["roblox_id"], group_id)

    if not bot_rutbe:
        await interaction.followup.send("Bot hesabi Roblox grubunun uyesi degil.", ephemeral=True)
        return None
    if not davetci_rutbe:
        await interaction.followup.send("Sizin dogruladiginiz Roblox hesabi bu grubun uyesi degil.", ephemeral=True)
        return None
    if not hedef_rutbe:
        await interaction.followup.send(f"{hedef_discord.mention} adli kisinin Roblox hesabi bu grubun uyesi degil.", ephemeral=True)
        return None

    try:
        roller = api.grup_rolleri(group_id)
    except RobloxAPIHatasi as e:
        await interaction.followup.send(f"Hata: {e}", ephemeral=True)
        return None

    return data, ayar, api, group_id, davetci_rutbe, hedef_rutbe, hedef_kayit["roblox_id"], roller, bot_rutbe


# ---------------------------------------------------------------------------
# /rutbe-degistir
# ---------------------------------------------------------------------------

@bot.tree.command(name="rutbe-degistir", description="Belirttiginiz uyeye belirli bir Roblox rutbesini verir.")
@app_commands.describe(uye="Rutbesi degistirilecek Discord uyesi", rutbe="Verilecek Roblox rutbe adi (grubunuzdaki rutbe ismiyle ayni olmali)")
async def rutbe_degistir(interaction: discord.Interaction, uye: discord.Member, rutbe: str):
    await interaction.response.defer(ephemeral=True)

    sonuc = await rutbe_islemi_hazirla(interaction, uye)
    if sonuc is None:
        return
    data, ayar, api, group_id, davetci_rutbe, hedef_rutbe, hedef_roblox_id, roller, bot_rutbe = sonuc

    hedef_rol = next((r for r in roller if r["name"].lower() == rutbe.lower()), None)
    if not hedef_rol:
        mevcut = ", ".join(r["name"] for r in roller)
        await interaction.followup.send(f"'{rutbe}' adinda bir rutbe bulunamadi. Mevcut rutbeler: {mevcut}", ephemeral=True)
        return

    izin, hata = yetki_kontrol(davetci_rutbe["rank"], hedef_rutbe["rank"], hedef_rol["rank"], bot_rutbe["rank"])
    if not izin:
        await interaction.followup.send(f"Yetki hatasi: {hata}", ephemeral=True)
        return

    r = api.rutbe_ayarla(group_id, hedef_roblox_id, hedef_rol["id"])
    if r.status_code not in (200, 204):
        await interaction.followup.send(f"Roblox API hatasi ({r.status_code}): {r.text[:300]}", ephemeral=True)
        return

    await interaction.followup.send(
        f"{uye.mention} adli kisinin rutbesi **{hedef_rutbe['name']}** -> **{hedef_rol['name']}** olarak degistirildi.",
        ephemeral=True,
    )


# ---------------------------------------------------------------------------
# /terfi
# ---------------------------------------------------------------------------

@bot.tree.command(name="terfi", description="Belirttiginiz uyeyi bir ust Roblox rutbesine cikarir.")
@app_commands.describe(uye="Terfi ettirilecek Discord uyesi")
async def terfi(interaction: discord.Interaction, uye: discord.Member):
    await interaction.response.defer(ephemeral=True)

    sonuc = await rutbe_islemi_hazirla(interaction, uye)
    if sonuc is None:
        return
    data, ayar, api, group_id, davetci_rutbe, hedef_rutbe, hedef_roblox_id, roller, bot_rutbe = sonuc

    mevcut_index = next((i for i, r in enumerate(roller) if r["id"] == hedef_rutbe["id"]), None)
    if mevcut_index is None or mevcut_index + 1 >= len(roller):
        await interaction.followup.send("Bu kisi zaten en ust rutbede ya da rutbesi bulunamadi.", ephemeral=True)
        return
    yeni_rol = roller[mevcut_index + 1]

    izin, hata = yetki_kontrol(davetci_rutbe["rank"], hedef_rutbe["rank"], yeni_rol["rank"], bot_rutbe["rank"])
    if not izin:
        await interaction.followup.send(f"Yetki hatasi: {hata}", ephemeral=True)
        return

    r = api.rutbe_ayarla(group_id, hedef_roblox_id, yeni_rol["id"])
    if r.status_code not in (200, 204):
        await interaction.followup.send(f"Roblox API hatasi ({r.status_code}): {r.text[:300]}", ephemeral=True)
        return

    await interaction.followup.send(
        f"{uye.mention} terfi ettirildi: **{hedef_rutbe['name']}** -> **{yeni_rol['name']}**", ephemeral=True
    )


# ---------------------------------------------------------------------------
# /tenzil
# ---------------------------------------------------------------------------

@bot.tree.command(name="tenzil", description="Belirttiginiz uyeyi bir alt Roblox rutbesine indirir.")
@app_commands.describe(uye="Rutbesi indirilecek Discord uyesi")
async def tenzil(interaction: discord.Interaction, uye: discord.Member):
    await interaction.response.defer(ephemeral=True)

    sonuc = await rutbe_islemi_hazirla(interaction, uye)
    if sonuc is None:
        return
    data, ayar, api, group_id, davetci_rutbe, hedef_rutbe, hedef_roblox_id, roller, bot_rutbe = sonuc

    mevcut_index = next((i for i, r in enumerate(roller) if r["id"] == hedef_rutbe["id"]), None)
    if mevcut_index is None or mevcut_index - 1 < 0:
        await interaction.followup.send("Bu kisi zaten en alt rutbede ya da rutbesi bulunamadi.", ephemeral=True)
        return
    yeni_rol = roller[mevcut_index - 1]

    izin, hata = yetki_kontrol(davetci_rutbe["rank"], hedef_rutbe["rank"], yeni_rol["rank"], bot_rutbe["rank"])
    if not izin:
        await interaction.followup.send(f"Yetki hatasi: {hata}", ephemeral=True)
        return

    r = api.rutbe_ayarla(group_id, hedef_roblox_id, yeni_rol["id"])
    if r.status_code not in (200, 204):
        await interaction.followup.send(f"Roblox API hatasi ({r.status_code}): {r.text[:300]}", ephemeral=True)
        return

    await interaction.followup.send(
        f"{uye.mention} tenzil edildi: **{hedef_rutbe['name']}** -> **{yeni_rol['name']}**", ephemeral=True
    )


# ---------------------------------------------------------------------------
# Yetki hatasi (admin olmayan) icin genel handler
# ---------------------------------------------------------------------------

@grup_bagla.error
@bot_hesap_bagla.error
@ayarlar.error
async def yonetici_hata_handler(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        msg = "Bu komutu kullanmak icin Yonetici (Administrator) yetkisine sahip olmalisiniz."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    else:
        raise error


# ---------------------------------------------------------------------------
# BASLAT
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if TOKEN == "BURAYA_DISCORD_BOT_TOKENINIZI_YAZIN":
        print("UYARI: DISCORD_TOKEN ortam degiskenini ayarlamadiniz veya TOKEN degiskenini duzenlemediniz.")
    bot.run(TOKEN)
