import sys

from src.agent import run_agent
from src.runtime import local_runtime


MAX_COLLECTION_STEPS = 6

DEFAULT_GOAL = (
    "Przeanalizuj subskrypcje i zaproponuj oszczednosci zgodne "
    "z preferencjami. Szczegolnie uwzglednij ostatnie uzywanie, "
    "rodzaje umow i ograniczenia. Zweryfikuj koncowa propozycje."
)


def main():
    goal = " ".join(sys.argv[1:]).strip() or DEFAULT_GOAL

    try:
        with local_runtime() as (base_url, model):
            result = run_agent(
                base_url=base_url,
                model=model,
                goal=goal,
                max_steps=MAX_COLLECTION_STEPS,
            )

            if result and result.startswith((
                "Agent zatrzymany",
                "Osiagnieto limit",
                "Przerwano",
            )):
                print("\n" + result)

    except KeyboardInterrupt:
        print("\nPrzerwano program.")
        return 130

    except Exception as error:
        print(f"\nBlad uruchomienia: {error}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())