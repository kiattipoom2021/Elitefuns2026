"""SET100 stock universe — hardcoded list (update semi-annually after SET rebalance)

Last updated: 2026-06 (manual). อ้างอิงจาก SET index methodology.
Update เมื่อ SET ประกาศ rebalance (Jan/Jul ทุกปี).
"""

# 100 stocks (อิงตาม market cap + liquidity, ~SET100 universe)
SET100_STOCKS: list[str] = [
    # Banks
    "BBL", "KBANK", "SCB", "KTB", "BAY", "TTB", "TISCO", "KKP", "TCAP",
    # Energy / Petro
    "PTT", "PTTEP", "GULF", "GPSC", "BANPU", "EGCO", "BCP", "IRPC",
    "TOP", "EA", "OR", "BCPG", "BPP", "ESSO",
    # Telecom
    "ADVANC", "TRUE", "INTUCH", "JAS", "JTS",
    # Retail / Consumer
    "CPALL", "CPN", "CRC", "BJC", "HMPRO", "MAKRO", "GLOBAL", "BEAUTY",
    "CPF", "TVO", "OSP", "CBG", "ICHI", "M", "MEGA", "MINT", "ERW",
    # Industrial / Materials
    "SCC", "SCGP", "TPIPL", "TASCO", "DELTA", "KCE", "HANA",
    # Real Estate / Construction
    "AMATA", "LH", "SPALI", "ORI", "AP", "LPN", "SIRI", "ANAN",
    "PSH", "CK", "STEC", "GLAND", "WHA",
    # Healthcare
    "BDMS", "BH", "BCH", "CHG", "EKH", "RAM", "RJH", "RPH", "BLA",
    # Transport / Logistics
    "AOT", "AAV", "BA", "BTS",
    # Tech / Media
    "BEC", "MAJOR", "MONO", "RS", "WORK", "INET", "SAMART", "SAMTEL", "ITEL",
    # Finance / Consumer Finance
    "KTC", "MTC", "SAWAD", "JMT", "AEONTS",
    # Misc / Smaller Cap
    "GUNKUL", "TFG", "ASIA", "PSL", "AMA", "AGE", "SMK", "SDC",
]
