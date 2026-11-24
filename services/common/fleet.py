"""A synthetic vehicle fleet using real makes and models sold in India.

The mix is skewed on purpose, so that a few model-and-colour combinations are common.
Generated vehicles and records are fictional. The skew makes "match on model and colour" hard at real
traffic density.
"""
from __future__ import annotations

# body -> (speed class, axles, share of all traffic, [(make, model), ...] most common first)
BODIES: dict[str, tuple[str, int, float, list[tuple[str, str]]]] = {
    "hatchback": ("car", 2, 0.24, [("Maruti Suzuki", "Swift"), ("Hyundai", "i20"), ("Tata", "Altroz"),
                                   ("Maruti Suzuki", "Baleno"), ("Hyundai", "Grand i10 Nios"),
                                   ("Tata", "Tiago"), ("Toyota", "Glanza"), ("Citroen", "C3")]),
    "sedan": ("car", 2, 0.10, [("Maruti Suzuki", "Dzire"), ("Honda", "City"), ("Hyundai", "Verna"),
                               ("Volkswagen", "Virtus"), ("Skoda", "Slavia"), ("Tata", "Tigor")]),
    "suv": ("car", 2, 0.20, [("Hyundai", "Creta"), ("Mahindra", "Thar"), ("Tata", "Nexon"),
                             ("Maruti Suzuki", "Brezza"), ("Kia", "Seltos"), ("Mahindra", "Scorpio-N"),
                             ("Toyota", "Fortuner"), ("Mahindra", "XUV700")]),
    "muv": ("car", 2, 0.06, [("Toyota", "Innova Crysta"), ("Maruti Suzuki", "Ertiga"), ("Kia", "Carens")]),
    "pickup": ("lcv", 2, 0.08, [("Mahindra", "Bolero Pik-Up"), ("Tata", "Yodha"), ("Isuzu", "D-Max")]),
    "van": ("lcv", 2, 0.06, [("Maruti Suzuki", "Eeco"), ("Tata", "Winger"), ("Force Motors", "Traveller")]),
    "bus": ("lcv", 2, 0.03, [("Tata", "Starbus"), ("Ashok Leyland", "Viking")]),
    "two_axle_truck": ("truck", 2, 0.10, [("Tata", "407 Gold"), ("Ashok Leyland", "Ecomet 1215"),
                                           ("BharatBenz", "1217R")]),
    "three_axle_truck": ("truck", 3, 0.08, [("Tata", "Signa 3518.T"), ("Ashok Leyland", "3520"),
                                             ("BharatBenz", "3523R")]),
    "trailer": ("truck", 5, 0.05, [("Tata", "Signa 5525.S"), ("Ashok Leyland", "5525")]),
}

COLOURS_PRIVATE = [("white", 0.36), ("silver", 0.14), ("grey", 0.14), ("red", 0.09), ("black", 0.08),
                   ("blue", 0.08), ("brown", 0.05), ("green", 0.03), ("orange", 0.03)]
COLOURS_COMMERCIAL = [("white", 0.45), ("brown", 0.15), ("yellow", 0.10), ("blue", 0.10),
                      ("red", 0.10), ("green", 0.05), ("orange", 0.05)]

# Colours a camera can plausibly confuse.
CONFUSION = {"white": ["silver"], "silver": ["white", "grey"], "grey": ["silver", "black"],
             "black": ["grey", "blue"], "red": ["orange", "brown"], "blue": ["black", "green"],
             "brown": ["red", "orange"], "green": ["blue"], "orange": ["red", "yellow"],
             "yellow": ["orange", "white"]}


def model_table() -> list[tuple[str, str, str, str, int, float]]:
    """Rows of (make, model, body, speed_class, axles, weight). Weights sum to 1."""
    rows = []
    for body, (speed_class, axles, share, models) in BODIES.items():
        zipf = [1.0 / (k + 1) for k in range(len(models))]
        total = sum(zipf)
        for (make, model), z in zip(models, zipf):
            rows.append((make, model, body, speed_class, axles, share * z / total))
    return rows


def speed_class_of(body: str) -> str:
    return BODIES[body][0] if body in BODIES else "car"


def colours_for(speed_class: str) -> list[tuple[str, float]]:
    return COLOURS_PRIVATE if speed_class == "car" else COLOURS_COMMERCIAL
