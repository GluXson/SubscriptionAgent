import json
from pathlib import Path


DATA = Path(__file__).resolve().parent.parent / "data"


def read_json(filename):
    return json.loads(
        (DATA / filename).read_text(encoding="utf-8")
    )


def read_preferences():
    preferences = read_json("preferences.json")

    if not isinstance(preferences, dict):
        raise ValueError("preferences.json musi zawierac obiekt.")

    return preferences


def read_subscriptions():
    records = read_json("subscriptions.json")
    preferences = read_preferences()

    if not isinstance(records, list):
        raise ValueError("subscriptions.json musi zawierac liste.")

    threshold = preferences.get("keep_if_used_within_days")

    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or threshold < 0
    ):
        raise ValueError(
            "keep_if_used_within_days musi byc nieujemna liczba."
        )

    protected = preferences.get("do_not_cancel", [])

    if not isinstance(protected, list) or any(
        not isinstance(name, str) for name in protected
    ):
        raise ValueError("do_not_cancel musi byc lista nazw.")

    protected_names = {
        name.casefold()
        for name in protected
    }

    avoid_penalties = preferences.get(
        "avoid_early_termination_penalties",
        False,
    )

    if not isinstance(avoid_penalties, bool):
        raise ValueError(
            "avoid_early_termination_penalties musi byc true lub false."
        )

    output = []

    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Kazda subskrypcja musi byc obiektem.")

        name = record["name"]
        days = record["days_since_last_use"]
        price = record["monthly_price_pln"]
        contract = record["contract"]

        if not isinstance(name, str):
            raise ValueError("Nazwa subskrypcji musi byc tekstem.")

        for field, value in (
            ("days_since_last_use", days),
            ("monthly_price_pln", price),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value < 0
            ):
                raise ValueError(
                    f"{name}: {field} musi byc nieujemna liczba."
                )

        if not isinstance(contract, str):
            raise ValueError(f"{name}: contract musi byc tekstem.")

        recent = days <= threshold
        explicitly_protected = name.casefold() in protected_names
        penalty_blocked = (
            avoid_penalties
            and contract == "annual_with_penalty"
        )

        reasons = []

        if recent:
            reasons.append(
                f"Niedawne uzywanie: {days} <= {threshold} dni."
            )

        if explicitly_protected:
            reasons.append("Usluga jest na liscie do_not_cancel.")

        if penalty_blocked:
            reasons.append(
                "Umowa roczna z kara; preferencje zabraniaja kar."
            )

        # Nie zakładamy, że nieznany typ umowy jest bezpieczny.
        unknown_contract = contract not in {
            "monthly",
            "annual_with_penalty",
        }

        if unknown_contract:
            reasons.append(
                "Nieznany rodzaj umowy: potrzebne dodatkowe dane."
            )

        eligible = not reasons

        output.append({
            **record,
            "activity_assessment": {
                "threshold_days": threshold,
                "comparison": (
                    f"{days} <= {threshold}"
                    if recent
                    else f"{days} > {threshold}"
                ),
                "recently_used": recent,
                "label": (
                    "NIEDAWNO_UZYWANA"
                    if recent
                    else "NIEUZYWANA_PONAD_PROG"
                ),
            },
            "cancellation_assessment": {
                "eligible_under_recorded_preferences": eligible,
                "status": (
                    "KANDYDAT_DO_ANULOWANIA"
                    if eligible
                    else "POZOSTAW"
                ),
                "reasons": reasons or [
                    (
                        f"Nieuzywana ponad prog: {days} > "
                        f"{threshold} dni; brak blokady "
                        "w zapisanych ograniczeniach."
                    )
                ],
            },
        })

    return output


def sort_subscriptions(by, descending):
    fields = {
        "last_used": "days_since_last_use",
        "price": "monthly_price_pln",
        "name": "name",
        "contract": "contract",
    }

    if not isinstance(by, str) or by not in fields:
        raise ValueError(
            "by: last_used, price, name lub contract."
        )

    if not isinstance(descending, bool):
        raise ValueError("descending musi byc true albo false.")

    field = fields[by]

    def key(record):
        value = record[field]
        return value.casefold() if isinstance(value, str) else value

    return {
        "sorted_by": field,
        "descending": descending,
        "subscriptions": sorted(
            read_subscriptions(),
            key=key,
            reverse=descending,
        ),
    }


def read_contract_rules():
    path = DATA / "contract_rules.json"

    return {
        "source": "local_project_data",
        "contract_field_meanings": {
            "monthly": (
                "Umowa miesieczna. Brak automatycznego potwierdzenia "
                "zwrotu pieniedzy lub terminu wypowiedzenia."
            ),
            "annual_with_penalty": (
                "Umowa roczna z kara za wczesne zakonczenie. "
                "Wysokosc kary nie jest podana."
            ),
        },
        "subscription_terms": [
            {
                "name": record["name"],
                "contract": record["contract"],
                "assessment": record["cancellation_assessment"],
            }
            for record in read_subscriptions()
        ],
        "additional_rules": (
            read_json("contract_rules.json")
            if path.is_file()
            else None
        ),
        "notice": (
            "Nie pobrano regulaminow z internetu. "
            "Nie zakladaj prawa do zwrotu ani terminu wypowiedzenia "
            "bez odpowiednich danych."
        ),
    }


TOOLS = {
    "read_subscriptions": {
        "function": read_subscriptions,
        "arguments": set(),
    },
    "read_preferences": {
        "function": read_preferences,
        "arguments": set(),
    },
    "sort_subscriptions": {
        "function": sort_subscriptions,
        "arguments": {"by", "descending"},
    },
    "read_contract_rules": {
        "function": read_contract_rules,
        "arguments": set(),
    },
}


def execute_tool(name, arguments):
    if not isinstance(name, str) or name not in TOOLS:
        raise ValueError(f"Nieznane narzedzie: {name}")

    if not isinstance(arguments, dict):
        raise ValueError("arguments musi byc obiektem JSON.")

    specification = TOOLS[name]

    if set(arguments) != specification["arguments"]:
        raise ValueError(
            f"Nieprawidlowe argumenty dla {name}. "
            f"Wymagane: {sorted(specification['arguments'])}"
        )

    return specification["function"](**arguments)