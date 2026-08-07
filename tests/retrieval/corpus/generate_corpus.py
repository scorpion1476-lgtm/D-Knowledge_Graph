#!/usr/bin/env python3
"""Generate the retained retrieval corpus and its query set.

The corpus is a set of short, single-topic documents on clearly distinct
subjects (databases, astronomy, baking, biology, networking, and so on). Each
query is a natural-language question whose single relevant document is fixed by
construction (the topic it asks about). Because the topics are distinct, the
relevance judgement is unambiguous and known without any subjective labelling.

Some documents carry a second, differently-worded query so the query set is
larger than the document set and the metric does not hinge on any one query.

Run ``python tests/retrieval/corpus/generate_corpus.py`` to regenerate
``corpus.json`` and ``queries.json`` in place. The corpus is retained and
versioned; this generator documents exactly how it is built so anyone can
reproduce it. Metrics computed on it (mean reciprocal rank, nDCG, recall) are
deterministic given the same corpus and models.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

# (id, document text, [queries]) with each query's single relevant doc == id.
TOPICS: list[tuple[str, str, list[str]]] = [
    ("db_planner",
     "A database query planner selects an efficient execution plan for each statement. It uses table statistics and the available indexes to reduce disk reads and to choose a good join order.",
     ["how does a database decide the fastest way to run a statement",
      "what picks the join order and indexes for a sql query"]),
    ("star_life",
     "When a star similar to the Sun exhausts the hydrogen fuel in its core, it swells into a red giant and later sheds its outer layers, leaving behind a small dense white dwarf that slowly cools.",
     ["what happens to a sun-like star near the end of its life",
      "how does a star become a white dwarf"]),
    ("bread",
     "Baking bread relies on yeast fermenting the sugars in flour, producing carbon dioxide that makes the dough rise. Gluten strands trap the gas, and the oven heat sets the structure into a crumb.",
     ["why does bread dough rise when it bakes",
      "what role does yeast play in making a loaf"]),
    ("photosynthesis",
     "Photosynthesis lets green plants convert sunlight, water, and carbon dioxide into glucose and oxygen. Chlorophyll in the chloroplasts captures light energy that drives the chemical reactions.",
     ["how do plants turn sunlight into food",
      "what does chlorophyll do in a leaf"]),
    ("tcp",
     "The transmission control protocol provides reliable, ordered delivery of a byte stream over an unreliable network. It uses sequence numbers, acknowledgements, and retransmission to recover lost packets.",
     ["how does the internet make sure data arrives in order",
      "what mechanism resends lost network packets reliably"]),
    ("mortgage",
     "A fixed-rate mortgage spreads the repayment of a home loan over many years at a constant interest rate. Each monthly payment covers interest on the balance and a portion of the principal.",
     ["how is a home loan paid back over time at a steady rate"]),
    ("vaccine",
     "A vaccine trains the immune system by presenting a harmless piece of a pathogen, so memory cells can recognise and respond quickly if the real infection arrives later.",
     ["how does a vaccine prepare the body against disease"]),
    ("compost",
     "Composting turns kitchen scraps and yard waste into rich soil as microbes break down the organic matter. Turning the pile adds oxygen and speeds the decomposition into humus.",
     ["how do food scraps turn into garden soil"]),
    ("guitar",
     "A guitar makes sound when a plucked string vibrates and the hollow body resonates, amplifying the tone. Pressing a string against a fret shortens it and raises the pitch.",
     ["how does pressing a fret change the note on a guitar"]),
    ("glacier",
     "A glacier forms where snow accumulates faster than it melts and compresses into dense ice over centuries. Gravity makes the ice flow slowly downhill, carving valleys as it moves.",
     ["how does a river of ice shape a mountain valley"]),
    ("encryption",
     "Public-key encryption uses a pair of keys: a public key anyone can use to encrypt a message, and a private key only the recipient holds to decrypt it. This secures data without sharing a secret.",
     ["how can two people exchange secret messages without a shared password"]),
    ("marathon",
     "Marathon training builds endurance by gradually increasing weekly mileage, with long slow runs to teach the body to burn fat and store more glycogen for the 42 kilometre distance.",
     ["how do runners prepare their bodies for a long race"]),
    ("volcano",
     "A volcano erupts when molten rock, called magma, rises through a weakness in the crust. Dissolved gases expand as the pressure drops, driving lava, ash, and rock out of the vent.",
     ["what makes a volcano erupt with lava and ash",
      "why does magma burst out through the crust"]),
    ("coffee_roasting",
     "Roasting green coffee beans drives off moisture and triggers caramelisation and the Maillard reaction, developing the brown colour, aroma, and flavour. Longer roasts taste darker and less acidic.",
     ["how does roasting change the flavour of coffee beans",
      "why do darker roasted beans taste less acidic"]),
    ("honeybee",
     "A honeybee colony divides labour among a queen who lays eggs, workers who forage and build comb, and drones. Foragers communicate the direction of flowers with a waggle dance.",
     ["how do bees in a hive share work and find flowers",
      "what does the waggle dance tell other bees"]),
    ("solar_panel",
     "A solar panel converts sunlight into electricity using photovoltaic cells. Photons knock electrons loose in a silicon layer, and the built-in electric field pushes them into a current.",
     ["how does a solar panel make electricity from the sun",
      "what happens inside a photovoltaic cell in sunlight"]),
    ("antibiotic",
     "Antibiotic resistance arises when bacteria that survive a drug pass on protective genes. Overusing antibiotics selects for these resistant strains, making later infections harder to treat.",
     ["why do some bacteria stop responding to medicine",
      "how does overusing antibiotics breed resistant bacteria"]),
    ("tide",
     "Ocean tides rise and fall mainly because of the Moon's gravity pulling on the seas, with the Sun contributing. The bulge of water sweeps around the Earth as the planet rotates.",
     ["what causes the sea level to rise and fall each day"]),
    ("jet_engine",
     "A jet engine takes in air, compresses it, mixes it with fuel, and ignites the mixture. The hot exhaust rushing out the back produces thrust and spins a turbine that drives the compressor.",
     ["how does a jet engine push an aircraft forward"]),
    ("coral_reef",
     "A coral reef is built by tiny animals called polyps that secrete calcium carbonate skeletons. Symbiotic algae living in the polyps provide food through photosynthesis and give the reef colour.",
     ["how do small sea creatures build a reef"]),
    ("violin",
     "A violin produces sound when a bow drags across a string, making it vibrate. The bridge transfers the vibration to the wooden body, whose shape and varnish shape the instrument's tone.",
     ["how does drawing a bow across a string make music"]),
    ("thunderstorm",
     "A thunderstorm develops when warm moist air rises rapidly, cooling and forming towering clouds. Charge separation inside the cloud builds until a lightning discharge heats the air into thunder.",
     ["what causes lightning and thunder in a storm"]),
    ("sourdough",
     "A sourdough starter is a living culture of wild yeast and lactic acid bacteria kept alive by regular feeding with flour and water. It leavens bread and gives it a tangy, sour flavour.",
     ["what is the living culture that makes tangy bread rise"]),
    ("river_delta",
     "A river delta forms where a river meets a slower body of water and drops its sediment. The load spreads into branching channels, building fertile fan-shaped land over time.",
     ["how does sediment build land where a river meets the sea"]),
    ("satellite",
     "A satellite stays in orbit because its forward speed balances the pull of gravity, so it keeps falling around the planet. Higher orbits need less speed and take longer to circle.",
     ["why does a satellite keep circling the earth instead of falling"]),
    ("wind_turbine",
     "A wind turbine converts the kinetic energy of moving air into electricity. The wind spins aerodynamic blades that turn a shaft connected through a gearbox to a generator.",
     ["how does wind get turned into electric power"]),
    ("cheese",
     "Cheese making starts by curdling milk with rennet and cultures, separating solid curds from liquid whey. Ageing lets enzymes and microbes develop the texture and sharp flavour.",
     ["how is milk turned into aged cheese"]),
    ("chess",
     "A chess opening aims to control the centre, develop the knights and bishops, and keep the king safe by castling. Good opening play sets up the middlegame rather than winning material at once.",
     ["what are the goals of a good chess opening"]),
    ("earthquake",
     "An earthquake happens when stress along a geological fault overcomes friction and the rock slips suddenly. The released energy travels outward as seismic waves that shake the ground.",
     ["why does the ground shake when a fault slips"]),
    ("hummingbird",
     "A hummingbird has an extremely fast metabolism, beating its wings dozens of times per second to hover. It feeds on energy-rich nectar and can enter a slowed torpor state overnight to save fuel.",
     ["how does a tiny hovering bird fuel its rapid wingbeats"]),
]


def main() -> int:
    documents = [{"id": tid, "text": text} for tid, text, _ in TOPICS]
    queries = []
    qn = 0
    for tid, _text, qs in TOPICS:
        for q in qs:
            qn += 1
            queries.append({"id": f"q{qn}", "text": q, "relevant": [tid]})

    corpus = {
        "note": (
            "Retained retrieval corpus of single-topic documents on distinct "
            "subjects. Generated by generate_corpus.py; relevance is known by "
            "construction (one relevant document per query, its topic)."
        ),
        "documents": documents,
    }
    query_obj = {
        "note": "Generated by generate_corpus.py. Each query's single relevant doc is its topic.",
        "queries": queries,
    }
    (HERE / "corpus.json").write_text(json.dumps(corpus, indent=2) + "\n", encoding="utf-8")
    (HERE / "queries.json").write_text(json.dumps(query_obj, indent=2) + "\n", encoding="utf-8")
    print(f"documents: {len(documents)} ; queries: {len(queries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
