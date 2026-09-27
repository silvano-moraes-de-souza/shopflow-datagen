"""Static reference data used to make the synthetic tables look like a Brazilian store.

Weights are rough shapes chosen by hand, not statistics. They exist so that
distributions are skewed the way real e-commerce data is skewed (a few states,
products and customers concentrate most of the volume).
"""

from __future__ import annotations

# Approximate share of population per state (rounded, sums to ~100).
STATES: dict[str, float] = {
    "SP": 21.9, "MG": 10.0, "RJ": 7.9, "BA": 6.9, "PR": 5.6, "RS": 5.3, "PE": 4.6,
    "CE": 4.5, "PA": 4.0, "SC": 3.7, "GO": 3.5, "MA": 3.4, "PB": 2.0, "AM": 1.9,
    "ES": 1.9, "MT": 1.8, "RN": 1.6, "PI": 1.6, "AL": 1.5, "DF": 1.4, "MS": 1.4,
    "SE": 1.1, "RO": 0.8, "TO": 0.7, "AC": 0.4, "AP": 0.4, "RR": 0.3,
}  # fmt: skip

# Flat shipping fee in cents by region, used when the order is below the
# free-shipping threshold.
SHIPPING_CENTS_BY_REGION = {"SE": 1590, "S": 1890, "CO": 2290, "NE": 2690, "N": 3290}
REGION_OF_STATE = {
    **dict.fromkeys(["SP", "RJ", "MG", "ES"], "SE"),
    **dict.fromkeys(["PR", "SC", "RS"], "S"),
    **dict.fromkeys(["GO", "MT", "MS", "DF"], "CO"),
    **dict.fromkeys(["BA", "PE", "CE", "MA", "PB", "RN", "PI", "AL", "SE"], "NE"),
    **dict.fromkeys(["PA", "AM", "RO", "TO", "AC", "AP", "RR"], "N"),
}
FREE_SHIPPING_THRESHOLD_CENTS = 19_900

FIRST_NAMES = [
    "Ana", "Maria", "Juliana", "Fernanda", "Camila", "Beatriz", "Larissa", "Patricia",
    "Aline", "Bruna", "Gabriela", "Leticia", "Mariana", "Renata", "Vanessa", "Carla",
    "Joao", "Pedro", "Lucas", "Gabriel", "Rafael", "Bruno", "Felipe", "Gustavo",
    "Rodrigo", "Thiago", "Marcelo", "Andre", "Diego", "Carlos", "Eduardo", "Silvano",
]  # fmt: skip
LAST_NAMES = [
    "Silva", "Santos", "Oliveira", "Souza", "Rodrigues", "Ferreira", "Alves", "Pereira",
    "Lima", "Gomes", "Costa", "Ribeiro", "Martins", "Carvalho", "Almeida", "Lopes",
    "Soares", "Fernandes", "Vieira", "Barbosa", "Rocha", "Dias", "Nascimento", "Moraes",
]  # fmt: skip
EMAIL_DOMAINS = ["gmail.com", "hotmail.com", "outlook.com", "yahoo.com.br", "uol.com.br"]
EMAIL_DOMAIN_WEIGHTS = [0.55, 0.2, 0.12, 0.08, 0.05]

# category -> (median price in BRL, lognormal sigma, share of catalog)
CATEGORIES: dict[str, tuple[float, float, float]] = {
    "electronics": (899.0, 0.9, 0.10),
    "computers": (1899.0, 0.8, 0.05),
    "home": (129.0, 0.8, 0.15),
    "kitchen": (89.0, 0.7, 0.10),
    "fashion": (119.0, 0.6, 0.18),
    "beauty": (59.0, 0.6, 0.12),
    "sports": (149.0, 0.8, 0.08),
    "toys": (79.0, 0.7, 0.07),
    "books": (49.0, 0.4, 0.08),
    "pet": (69.0, 0.6, 0.07),
}
BRANDS = [
    "Aurora", "Vertex", "Nimbus", "Pampa", "Cerrado", "Litoral", "Serra", "Atlas",
    "Boreal", "Ipe", "Jatoba", "Maracuja", "Orion", "Pixel", "Quasar", "Tupi",
]  # fmt: skip

CHANNELS = ["web", "app", "marketplace"]
CHANNEL_WEIGHTS = [0.45, 0.40, 0.15]

PAYMENT_METHODS = ["pix", "credit_card", "boleto", "debit_card"]
PAYMENT_METHOD_WEIGHTS = [0.40, 0.45, 0.10, 0.05]

# Hour-of-day demand curve (local time), 24 values.
HOUR_WEIGHTS = [
    0.8, 0.5, 0.3, 0.2, 0.2, 0.3, 0.6, 1.0, 1.5, 2.0, 2.3, 2.4,
    2.5, 2.4, 2.3, 2.3, 2.4, 2.6, 2.9, 3.3, 3.6, 3.4, 2.6, 1.6,
]  # fmt: skip
# Monday..Sunday
WEEKDAY_WEIGHTS = [1.05, 1.05, 1.0, 1.0, 0.95, 0.85, 0.9]
