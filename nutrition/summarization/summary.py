"""Part 2 of the brief: a written summary of the menu's nutrition, streamed as it is generated."""
from . import client
from .context import SYSTEM_RULES, facts

FOCUS_PROMPTS = {
    "overview": "Give a balanced overview of the whole menu.",
    "sugar": ("Focus on sugar: which items are highest and lowest, and how drinks compare with food. If sugar is missing "
              "from a file, say so in the first takeaway and rank by carbs instead, calling them carbs (never 'sweet')."),
    "calories": ("Focus on calories: the most calorie-dense items (calories per item; the files have no serving weights, "
                 "so say 'per item') and the lightest, plus the heaviest and lightest categories."),
    "protein": "Focus on protein and fat: best protein sources, fat-to-protein balance, and lean options.",
}


def _sections(frames: dict) -> str:
    """The section list for the prompt; with one dataset there is nothing to compare."""
    if {"drinks", "food"} <= set(frames):
        return "'Key takeaways' (3 bullets), 'Drinks', 'Food', 'Drinks vs food', 'Lighter choices'"
    only = "Drinks" if "drinks" in frames else "Food"
    return f"'Key takeaways' (3 bullets), '{only}', 'Lighter choices' (only the {only.lower()} file was provided, so do not compare)"


def _menu_facts(kind: str) -> str:
    """The fact bullets for one dataset in the overview."""
    caffeine = ("\n  - **Caffeine:** how many are caffeinated, and the highest in mg when DATA has mg values"
                if kind == "drinks" else "")
    return (f"'## {kind.title()}': exactly these bullets, each '**Label:** …' with values and units, no extra sentences:\n"
            "  - **Size:** item count and number of categories\n"
            "  - **Heaviest / lightest:** the highest and lowest calorie item\n"
            "  - **Most sugar:** top item (or **Most carbs** when sugar is missing)\n"
            "  - **Most protein:** top item\n"
            "  - **Categories:** the highest and the lowest average-calorie category, with their averages"
            + caffeine
            + ("" if kind == "drinks" else "\n  (no caffeine bullet for food)"))


def _overview_format(frames: dict) -> str:
    """A fixed, scannable layout for the Overview focus: short bullets and tables instead of paragraphs."""
    both = {"drinks", "food"} <= set(frames)
    kinds = [k for k in ("drinks", "food") if k in frames]
    parts = ["'## Key takeaways': exactly 4 bullets, each a bold 2-4 word label then one short sentence with numbers. "
             "Cover calories, the biggest nutrient difference, caffeine, and the best lighter option. Say a menu is higher "
             "or lower only as the 'higher' field states; quote 'times_higher', never work out ratios."]
    if both:
        parts.append("'## At a glance': a Markdown table Nutrient | Drinks avg (range) | Food avg (range) | Higher, one row per "
                     "nutrient in 'drinks compared with food'. Cells like '138.6 kcal (0–430)'. Higher is like 'Food, 2.57×' "
                     "using times_higher. Nutrient is the plain name (Calories, Fat...).")
    else:
        only = kinds[0]
        parts.append(f"'## At a glance': a Markdown table Nutrient | Average | Median | Range | Highest item for {only}, "
                     f"one row per nutrient, values with units. Under it one line: only the {only} file was provided, "
                     "so there is no comparison with the other menu.")
    parts.append("'## Where the calories come from': a Markdown table Menu | Carbs | Fat | Protein | Fat-to-protein, "
                 "one row per menu, using the calorie shares (%) and the fat-to-protein ratio from DATA.")
    parts += [_menu_facts(kind) for kind in kinds]
    picks = "'**Drinks:**' and '**Food:**' bullets, each with up to 3 items" if both else "one bullet per item, up to 3 items"
    parts.append(f"'## Lighter choices': {picks}, each 'item (kcal)', taken only from the lowest calorie lists in DATA "
                 "(never add other items).")
    parts.append("'## About the data': one line: rows removed while cleaning per menu, and nutrients missing from each file.")
    return ("Write a structured overview, at most 420 words, with exactly these sections in this order:\n- "
            + "\n- ".join(parts)
            + "\nUse only bullets and tables, no paragraphs. Write section titles as level-2 Markdown headings (## Title).")


def stream_summary(frames: dict, reports: dict, focus: str = "overview"):
    """Return a generator of text chunks. Raises LLMError before streaming if setup fails."""
    groq_client = client.get_client()
    if focus in FOCUS_PROMPTS and focus != "overview":
        request = (f"{FOCUS_PROMPTS[focus]}\n"
                   f"Write a nutrition summary of at most 300 words with these sections: {_sections(frames)}. "
                   "Write every section title as a level-2 markdown heading (## Title), never bold text. "
                   "Cite specific items with their numbers.")
    else:  # the overview carries the most information, so it gets a fixed layout of bullets and a table
        request = f"{FOCUS_PROMPTS['overview']}\n{_overview_format(frames)}"
    messages: list[client.Message] = [
        {"role": "system", "content": SYSTEM_RULES + "\n\nDATA:\n" + facts(frames, reports)},
        {"role": "user", "content": request},
    ]
    try:
        stream = groq_client.chat.completions.create(messages=messages, stream=True, temperature=0.2, **client.MODEL_OPTIONS)
    except Exception as exc:
        raise client.friendly_error(exc) from exc

    # The request is made above so setup errors (bad key, rate limit) surface as a normal
    # HTTP error. Errors that happen mid-stream can only be appended to the text itself.
    def chunks():
        try:
            for part in stream:
                text = part.choices[0].delta.content if part.choices else None
                if text:
                    yield text
        except Exception as exc:
            yield f"\n\n*The summary stopped early: {client.friendly_error(exc)}*"

    return chunks()
