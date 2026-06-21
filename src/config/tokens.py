"""Token allowlists for Plan B+ trading decisions."""

from __future__ import annotations

# Official BNB Hack eligible BEP-20 list supplied from the rules page. The
# rules list includes SLX twice; the duplicate is preserved intentionally.
ELIGIBLE_149_SYMBOLS: list[str] = [
    "ETH",
    "USDT",
    "USDC",
    "XRP",
    "TRX",
    "DOGE",
    "ZEC",
    "ADA",
    "LINK",
    "BCH",
    "DAI",
    "TON",
    "USD1",
    "USDe",
    "M",
    "LTC",
    "AVAX",
    "SHIB",
    "XAUt",
    "WLFI",
    "H",
    "DOT",
    "UNI",
    "ASTER",
    "DEXE",
    "USDD",
    "ETC",
    "AAVE",
    "ATOM",
    "U",
    "STABLE",
    "FIL",
    "INJ",
    "币安人生",
    "NIGHT",
    "FET",
    "TUSD",
    "BONK",
    "PENGU",
    "CAKE",
    "SIREN",
    "LUNC",
    "ZRO",
    "KITE",
    "FDUSD",
    "BEAT",
    "PIEVERSE",
    "BTT",
    "NFT",
    "EDGE",
    "FLOKI",
    "LDO",
    "B",
    "FF",
    "PENDLE",
    "NEX",
    "STG",
    "AXS",
    "TWT",
    "HOME",
    "RAY",
    "COMP",
    "GWEI",
    "XCN",
    "GENIUS",
    "XPL",
    "BAT",
    "SKYAI",
    "APE",
    "IP",
    "SFP",
    "TAG",
    "NXPC",
    "AB",
    "SAHARA",
    "1INCH",
    "CHEEMS",
    "BANANAS31",
    "RIVER",
    "MYX",
    "RAVE",
    "SNX",
    "FORM",
    "LAB",
    "HTX",
    "USDf",
    "CTM",
    "BDX",
    "SLX",
    "UB",
    "DUCKY",
    "FRAX",
    "BILL",
    "WFI",
    "KOGE",
    "ALE",
    "FRXUSD",
    "USDF",
    "GOMINING",
    "VCNT",
    "GUA",
    "DUSD",
    "SMILEK",
    "0G",
    "BEAM",
    "MY",
    "SLX",
    "SOON",
    "REAL",
    "Q",
    "AIOZ",
    "ZIG",
    "YFI",
    "TAC",
    "lisUSD",
    "CYS",
    "ZAMA",
    "TRIA",
    "HUMA",
    "PLUME",
    "ZIL",
    "XPR",
    "ZETA",
    "BabyDoge",
    "NILA",
    "ROSE",
    "VELO",
    "UAI",
    "BRETT",
    "OPEN",
    "BSB",
    "TOSHI",
    "BAS",
    "ACH",
    "AXL",
    "LUR",
    "ELF",
    "KAVA",
    "APR",
    "IRYS",
    "EURI",
    "XUSD",
    "BARD",
    "DUSK",
    "SUSHI",
    "PEAQ",
    "COAI",
    "BDCA",
    "XAUM",
]

# Deduplicated operational universe (148 unique symbols; SLX appears twice in rules).
TARGET_SYMBOLS: list[str] = list(dict.fromkeys(ELIGIBLE_149_SYMBOLS))

TARGET_SYMBOL_BY_KEY: dict[str, str] = {symbol.upper(): symbol for symbol in TARGET_SYMBOLS}

STABLE_TARGET_SYMBOLS: set[str] = {
    "USDT",
    "USDC",
    "DAI",
    "USD1",
    "USDE",
    "USDD",
    "TUSD",
    "FDUSD",
    "USDF",
    "FRXUSD",
    "USDF",
    "DUSD",
    "LISUSD",
    "XUSD",
    "EURI",
    "FRAX",
}

MOMENTUM_EXCLUDED: set[str] = {
    "USDT",
    "USDC",
    "DAI",
    "USD1",
    "USDE",
    "TUSD",
    "FDUSD",
    "USDD",
    "FRAX",
    "FRXUSD",
    "USDF",
    "LISUSD",
    "XUSD",
    "EURI",
    "DUSD",
    "STABLE",
    "XAUT",
    "XAUM",
}

TRADABLE_TARGET_SYMBOLS: list[str] = [
    symbol for symbol in TARGET_SYMBOLS if symbol.upper() not in STABLE_TARGET_SYMBOLS
]

TOKEN_CONTRACTS_BSC: dict[str, str] = {
    "ETH": "0x2170Ed0880ac9A755fd29B2688956BD959F933F8",
    "USDT": "0x55d398326f99059fF775485246999027B3197955",
    "USDC": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
    "CAKE": "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82",
    "LINK": "0xF8A0BF9cF54Bb92F17374d9e9A321E6a111a51bD",
    "AAVE": "0xfb6115445Bff7b52FeB98650C87f44907E58f802",
    "UNI": "0xBf5140A22578168FD562DCcF235E5D43A02ce9B1",
    "INJ": "0xA2B726b114B60cecCb8Af0BC6f6602C51928564C",
    "SHIB": "0x2859e4544C4bB03966803b044A93563Bd2D0DD4D",
    "DOGE": "0xbA2aE424d960c26247Dd6c32edC70B295c744C43",
    "BONK": "0xA697e272a73744b343528C3Bc4702F2565b2F422",
    "FLOKI": "0xfb5B838b6cfEEdC2873aB27866079AC55363D37E",
    "WBNB": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
    "BTCB": "0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c",
    "ADA": "0x3EE2200Efb3400fAbB9AacF31297cBdD1d435D47",
    "XRP": "0x1D2F0da169ceB9fC7B3144628dB156f3F6c60dBE",
    "DOT": "0x7083609fCE4d1d8Dc0C979AAb8c869Ea2C873402",
    "LTC": "0x4338665CBB7B2485A8855A139b75D5e34AB0DB94",
    "ATOM": "0x0Eb3a705fc54725037CC9e008bDede697f62F335",
    "FIL": "0x0D8Ce2A99Bb6e3B7Db580eD848240e4a0F9aE153",
    "TRX": "0x85EAC5Ac2F758618dFa09bDbe0cf174e7d574D5B",
    "TON": "0x76A797A59Ba2C17726896976B7B3747BfD1d220f",
    "AVAX": "0x1CE0c2827e2eF14D5C4f29a091d735A204794041",
    # Extended for hackathon — verified via onchain symbol() check
    "FET": "0x031b41e504677879370e9DBcF937283A8691Fa7f",
    "AXS": "0x715D400F88C167884bbCc41C5FeA407ed4D2f8A0",
    "TWT": "0x4B0F1812e5Df2A09796481Ff14017e6005508003",
    "SNX": "0x9Ac983826058b8a9C7Aa1C9171441191232E8404",
    "COMP": "0x52CE071Bd9b1C4B00A0b92D298c512478CaD67e8",
    "BAT": "0x101d82428437127bF1608F699CD651e6Abf9766E",
    # SUSHI: BSC address unverified — omitted until confirmed
    "YFI": "0x88f1A5ae2A3BF98AEAF342D26B30a79438c9142e",
    "1INCH": "0x111111111117dC0aa78b770fA6A738034120C302",
    "ZIL": "0xb86AbCb37C3A4B64f74f59301AFF131a1BEcC787",
    "BCH": "0x8fF795a6F4D97E7887C79beA79aba5cc76444aDf",
    "ETC": "0x3d6545b08693daE087E957cb1180ee38B9e3c25E",
    "ZEC": "0x1Ba42e5193dfA8B03D15dd1B86a3113bbBEF8Eeb",
    "STG": "0xB0D502E938ed5f4df2E681fE6E419ff29631d62b",
    "PENDLE": "0xb3Ed0A426155B79B898849803E3B36552f7ED507",
    "ACH": "0xBc7d6B50616989655AfD682fb42743507003056D",
    "BTT": "0x352Cb5E19b12FC216548a2677bD0fce83BaE434B",
    "KAVA": "0x5F88AB06e8dfe89DF127B2430Bba4Af600866035",
    "APE": "0xC762043E211571eB34f1ef377e5e8e76914962f9",
    "ZRO": "0x6985884C4392D348587B19cb9eAAf157F13271cd",
    "LDO": "0x986854779804799C1d68867F5E03e601E781e41b",
    "AXL": "0x8b1f4432F943c465A973FeDC6d7aa50Fc96f1f65",
    # SUSHI omitted — BSC address unverified
    # ── Hackathon universe expansion (June 2026) ──────────────────────────────
    # Sources: PancakeSwap extended list + CoinGecko BSC list.
    # Verification: onchain symbol() match (64), name()+bytecode only (20),
    # known-mismatch kept (BANANAS31 onchain=$BANANA; LUNC onchain=LUNA legacy).
    # GOMINING (onchain=GMT/StepN) and ROSE (onchain=wROSE) excluded.
    "0G": "0x4B948d64dE1F71fCd12fB586f4c776421a35b3eE",
    "AB": "0x95034f653D5D161890836Ad2B6b8cc49D14e029a",
    "AIOZ": "0x33d08D8C7a168333a85285a68C0042b39fC3741D",
    "ALE": "0x9dCE13E71B11eb5Df66ca269bD657696587Fd4E2",
    "APR": "0x299AD4299Da5b2b93Fba4c96967B040C7F611099",
    "ASTER": "0x000Ae314E2A2172a039B26378814C252734f556A",
    "B": "0x6bdcCe4A559076e37755a78Ce0c06214E59e4444",
    "BABYDOGE": "0xc748673057861a797275CD8A068AbB95A902e8de",
    "TOSHI": "0x6a2608Dabe09bc1128EEC7275B92DFB939D5Db3f",
    "BANANAS31": "0x3d4f0513E8a29669B960f9dBcA61861548A9A760",
    "BARD": "0xd23A186A78c0B3B805505E5f8ea4083295ef9f3a",
    "BAS": "0x0F0df6cB17ee5E883eddFEf9153fC6036BDB4e37",
    "BDCA": "0x0c8382719ef242CaE2247E4DeCb2891fBF699818",
    "BDX": "0x9d10a1ec41Fe7878429BB457e31F9b050D38c633",
    "BEAM": "0x62D0A8458eD7719FDAF978fe5929C6D342B0bFcE",
    "BEAT": "0xcf3232B85b43BCa90E51D38cc06Cc8bB8C8A3E36",
    "BILL": "0xDf24f8c21Cb404B3031a450D8e049D6E39FC1fA5",
    "BSB": "0x595dEaad1eB5476Ff1E649fDb7EFC36F1E4679cc",
    "CHEEMS": "0x0DF0587216a4a1bB7d5082fdc491d93d2dD4B413",
    "COAI": "0x0A8D6C86e1bcE73fE4D0bD531e1a567306836EA5",
    "CTM": "0xc8Fb80fCc03f699C70ff0CC08C09106288888888",
    "CYS": "0x0C69199C1562233640e0Db5Ce2c399A88eB507C7",
    "DEXE": "0x039cB485212f996A9DBb85A9a75d898F94d38dA6",
    "DUCKY": "0xE215f9575e2fAFff8D0D3F9C6866ac656bD25BD9",
    "DUSK": "0xB2BD0749DBE21f623d9BABa856D3B0f0e1BFEc9C",
    "EDGE": "0x70f2EADf1CA1969FF42b0c78e9DA519e8937cbaF",
    "ELF": "0xa3f020a5C92e15be13CAF0Ee5C95cF79585EeCC9",
    "EURI": "0x9d1A7A3191102e9F900Faa10540837ba84dCBAE7",
    "FF": "0xAC23B90A79504865D52B49B327328411a23d4dB2",
    "FORM": "0x25A528af62e56512A19ce8c3cAB427807c28CC19",
    "FRXUSD": "0x80Eede496655FB9047dd39d9f418d5483ED600df",
    "GENIUS": "0x1F12B85aAC097E43Aa1555b2881E98a51090e9A6",
    "GUA": "0xA5C8e1513B6A08334b479fe4D71F1253259469BE",
    "GWEI": "0x30117E4bC17d7B044194b76A38365C53b72F7D49",
    "H": "0x44F161aE29361E332dEA039DFA2F404E0bC5B5Cc",
    "HOME": "0x4BfAa776991E85e5f8b1255461cbbd216cFc714f",
    "HTX": "0x61EC85aB89377db65762E234C946b5c25A56E99e",
    "HUMA": "0x92516e0DDf1dDBF7FAB1b79CaC26689fDC5ba8e6",
    "IRYS": "0x91152B4Ef635403efBAe860edD0F8c321d7c035d",
    "KITE": "0x904567252D8F48555b7447c67dCA23F0372E16be",
    "KOGE": "0xe6DF05CE8C8301223373CF5B969AFCb1498c5528",
    "LAB": "0x7ec43Cf65F1663F820427C62A5780b8f2E25593A",
    "LUNC": "0x156ab3346823B651294766e23e6Cf87254d68962",
    "LUR": "0xc66B6f38aE5053A109cfd8639E0Ee17EC69cf788",
    "M": "0x22b1458e780F8fA71E2F84502cEe8B5A3cc731Fa",
    "MY": "0xF0EBB572643336834d516C485ad31d3299999999",
    "MYX": "0xD82544bf0dfe8385eF8FA34D67e6e4940CC63e16",
    "NEX": "0x365DE036A1F7dcCb621530d517133521debB2013",
    "NFT": "0x20eE7B720f4E4c4FFcB00C4065cdae55271aECCa",
    "NIGHT": "0xFe930c2d63AeD9b82fC4DBC801920dD2c1a3224F",
    "NILA": "0x00f8Da33734FeB9b946fEC2228C25072D2e2E41f",
    "NXPC": "0xf2b51CC1850fEd939658317a22d73d3482767591",
    "OPEN": "0xA227Cc36938f0c9E09CE0e64dfab226cad739447",
    "PEAQ": "0x8b9Ee39195eA99d6ddD68030F44131116bc218F6",
    "PENGU": "0x6418c0dd099a9FDA397C766304CDd918233E8847",
    "PIEVERSE": "0x0E63B9C287E32A05E6b9AB8ee8dF88A2760225A9",
    "PLUME": "0x5aFadCd1E8E3CA78Ee2D37100102f2aec8Bc0Aa8",
    "Q": "0xc07e1300dc138601FA6B0b59f8D0FA477e690589",
    "RAVE": "0x97693439EA2f0ecdeb9135881E49f354656a911c",
    "REAL": "0xE91cd52Bd65fe23A3EAE40E3eB87180E8306399F",
    "RIVER": "0xdA7AD9dea9397cffdDAE2F8a052B82f1484252B3",
    "SAHARA": "0xFDFfB411C4A70AA7C95D5C981a6Fb4Da867e1111",
    "SFP": "0xD41FDb03Ba84762dD66a0af1a6C8540FF1ba5dfb",
    "SIREN": "0x997A58129890bBdA032231A52eD1ddC845fc18e1",
    "SKYAI": "0x92aa03137385F18539301349dcfC9EbC923fFb10",
    "SLX": "0x8A063A9ff4dE28dcB87117cc759BE6cE70e09F81",
    "SMILEK": "0x4f9d3AdbfAF4579518b1Ca7E06468A363897B997",
    "SOON": "0xb9E1Fd5A02D3A33b25a14d661414E6ED6954a721",
    "STABLE": "0x011EBe7d75E2C9D1E0bD0be0bEf5C36f0A90075F",
    "TAG": "0x208bF3E7dA9639f1Eaefa2DE78c23396B0682025",
    "TRIA": "0xb0b92de23bAa85fB06208277E925ceD53edab482",
    "U": "0xba5eD44733953d79717F6269357C77718C8Ba5ed",
    "UAI": "0x3E5d4f8aee0D9B3082d5f6DA5D6e225D17ba9ea0",
    "UB": "0x40b8129B786D766267A7a118cF8C07E31CDB6Fde",
    "USD1": "0x8d0D000Ee44948FC98c9B98A4FA4921476f08B0d",
    "VCNT": "0xc6BDFC4f2E90196738873E824a9eFa03F7c64176",
    "VELO": "0xf486ad071f3bEE968384D2E39e2D8aF0fCf6fd46",
    "WFI": "0x90c48855Bb69f9D2C261Efd0D8C7F35990F2dd6f",
    "WLFI": "0x47474747477b199288bF72a1D702f7Fe0Fb1DEeA",
    "XAUM": "0x23AE4fd8E7844cdBc97775496eBd0E8248656028",
    "XCN": "0x7324c7C0d95CEBC73eEa7E85CbAac0dBdf88a05b",
    "XPL": "0x405FBc9004D857903bFD6b3357792D71a50726b0",
    "XPR": "0x5de3939b2F811A61d830E6F52d13B066881412ab",
    "ZAMA": "0x6907A5986C4950Bdaf2F81828Ec0737ce787519f",
    "ZETA": "0x0000028a2eB8346cd5c0267856aB7594B7a55308",
    "ZIG": "0x8C907e0a72C3d55627E853f4ec6a96b0C8771145",
    "BRETT": "0xA7440029ecA41dEaBd8775Ef1D6086b37d4dF8D6",
    "SUSHI": "0x947950BcC74888a40Ffa2593C5798F11Fc9124C4",
    "TAC": "0x1219c409faBe2C27Bd0D1A565daeed9Bd9f271dE",
}

# TWAK/LiquidMesh token identifiers (symbols resolved to BSC contract addresses).
TOKEN_CONTRACTS: dict[str, str] = {
    "BNB": "BNB",
    **TOKEN_CONTRACTS_BSC,
}

CMC_IDS_BY_SYMBOL: dict[str, str] = {
    # Pinned UCIDs disambiguate shared tickers: CMC's symbol lookup can return
    # dead knockoff listings (e.g. DOGE -> "Doge Grok Companion") with null
    # quotes. Verify additions with scripts/verify_cmc_ids.py before deploying.
    # Canonical assets recovered in the June 12 unpriced-tokens audit:
    "APE": "18876",
    "BAT": "1697",
    "BRETT": "29743",
    "BTT": "16086",
    "DAI": "4943",
    "ELF": "2299",
    "NFT": "9816",
    "ROSE": "7653",
    "SNX": "2586",
    "SUSHI": "6758",
    "TUSD": "2563",
    "TWT": "5964",
    # Original map:
    "ETH": "1027",
    "USDT": "825",
    "USDC": "3408",
    "CAKE": "7186",
    "LINK": "1975",
    "AAVE": "7278",
    "UNI": "7083",
    "INJ": "7226",
    "SHIB": "5994",
    "DOGE": "74",
    "BONK": "23095",
    "FLOKI": "10804",
    "BTC": "1",
    "BNB": "1839",
    "WBNB": "7192",
    "BTCB": "4023",
    "ADA": "2010",
    "XRP": "52",
    "DOT": "6636",
    "LTC": "2",
    "ATOM": "3794",
    "FIL": "2280",
    "TRX": "1958",
    "TON": "11419",
    "AVAX": "5805",
}

LIQUIDITY_BLACKLIST: set[str] = {
    "lisUSD",
    "ALE",
    "DUCKY",
    "SMILEK",
    "BDCA",
    "NILA",
    "LUR",
}

# One-week competition window: these hard floors avoid opening into tokens where
# slippage and thin books can dominate expected PnL without improving drawdown.
MIN_VOLUME_24H_USD = 5_000_000
MIN_MARKET_CAP_USD = 50_000_000


def is_target_symbol(symbol: str) -> bool:
    """Return whether a symbol is in the BNB Hack eligible target universe."""

    return symbol.strip().upper() in TARGET_SYMBOL_BY_KEY


def is_tradable_symbol(symbol: str) -> bool:
    """Return whether a symbol may be opened as a directional trade."""

    key = symbol.strip().upper()
    if key not in TARGET_SYMBOL_BY_KEY:
        return False
    return key not in STABLE_TARGET_SYMBOLS


def is_momentum_candidate_symbol(symbol: str) -> bool:
    """Return whether a symbol may be selected for a momentum entry."""

    key = symbol.strip().upper()
    if key not in TARGET_SYMBOL_BY_KEY:
        return False
    return key not in MOMENTUM_EXCLUDED


def is_liquid(token_data: dict[str, object]) -> bool:
    """Return whether a token clears hard blacklist and minimum liquidity floors."""

    symbol = str(token_data.get("symbol", "")).strip().upper()
    blacklisted = {blocked.upper() for blocked in LIQUIDITY_BLACKLIST}
    if symbol in blacklisted:
        return False
    volume_24h = _number(token_data.get("volume_24h"), 0.0)
    market_cap = _number(token_data.get("market_cap"), 0.0)
    return volume_24h >= MIN_VOLUME_24H_USD and market_cap >= MIN_MARKET_CAP_USD


def assert_target_symbol(symbol: str) -> None:
    """Raise when a token is outside the BNB Hack eligible target universe."""

    normalized = symbol.strip().upper()
    if normalized not in TARGET_SYMBOL_BY_KEY:
        raise ValueError(f"{normalized} is not in the TARGET_SYMBOLS allowlist")


def assert_tradable_symbol(symbol: str) -> None:
    """Raise when a token should not be opened as a directional trade."""

    normalized = symbol.strip().upper()
    if not is_tradable_symbol(normalized):
        raise ValueError(f"{normalized} is not in the tradable target allowlist")


def has_bsc_contract(symbol: str) -> bool:
    """Return whether a symbol is tradable as BEP-20 on BSC for this hackathon.

    The eligible universe is BSC-native; verified addresses are listed in
    ``TOKEN_CONTRACTS_BSC`` when known. TWAK resolves remaining hack symbols
    by ticker on BSC (see ``resolve_twak_token``).
    """

    normalized = symbol.strip().upper()
    if normalized in TOKEN_CONTRACTS_BSC or normalized == "BNB":
        return True
    return is_tradable_symbol(normalized)


def has_verified_bsc_contract(symbol: str) -> bool:
    """Return True only when a verified BEP-20 address exists for live TWAK execution."""

    normalized = symbol.strip().upper()
    return normalized in TOKEN_CONTRACTS_BSC or normalized == "BNB"


def resolve_twak_token(symbol: str) -> str:
    """Return the TWAK CLI token argument for a symbol or pass through addresses."""

    normalized = symbol.strip().upper()
    if normalized.startswith("0X") and len(normalized) == 42:
        return symbol.strip()
    return TOKEN_CONTRACTS.get(normalized, symbol.strip())


def get_bsc_token_address(symbol: str) -> str:
    """Return the verified BSC token identifier used by bnb-chain-agentkit."""

    normalized = symbol.strip().upper()
    assert_target_symbol(normalized)
    try:
        return TOKEN_CONTRACTS_BSC[normalized]
    except KeyError as exc:
        raise ValueError(
            f"No BSC contract configured for {normalized}; TWAK may still resolve the symbol directly"
        ) from exc


def resolve_cmc_coin_id(symbol: str) -> str | None:
    """Return a configured CoinMarketCap ID without TARGET_SYMBOLS gating."""

    return CMC_IDS_BY_SYMBOL.get(symbol.strip().upper())


def get_cmc_id_optional(symbol: str) -> str | None:
    """Return the CoinMarketCap ID when configured, otherwise None."""

    normalized = symbol.strip().upper()
    if normalized not in TARGET_SYMBOL_BY_KEY:
        return None
    return CMC_IDS_BY_SYMBOL.get(normalized)


def get_cmc_id_for_mcp(symbol: str) -> str:
    """Return the CoinMarketCap ID for MCP/x402 tool calls."""

    normalized = symbol.strip().upper()
    cmc_id = resolve_cmc_coin_id(normalized)
    if cmc_id is None:
        raise ValueError(f"No CoinMarketCap ID configured for MCP lookup: {normalized}")
    return cmc_id


def get_cmc_id(symbol: str) -> str:
    """Return the CoinMarketCap cryptocurrency ID for a target symbol."""

    normalized = symbol.strip().upper()
    assert_target_symbol(normalized)
    cmc_id = get_cmc_id_optional(normalized)
    if cmc_id is None:
        raise ValueError(f"No CoinMarketCap ID configured for {normalized}")
    return cmc_id


def _number(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
