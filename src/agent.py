import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

from src.prompt_var import (
    prompt_tools,
    prompt1,
    prompt2,
    prompt3,
    prompt4,
)
from src.tools import execute_tool


ROOT = Path(__file__).resolve().parent.parent


TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["tool", "finish"],
        },
        "tool": {
            "type": "string",
            "enum": [
                "",
                "read_preferences",
                "read_subscriptions",
                "sort_subscriptions",
                "read_contract_rules",
            ],
        },
        "arguments": {
            "type": "object",
            "properties": {
                "by": {
                    "type": "string",
                    "enum": [
                        "last_used",
                        "price",
                        "name",
                        "contract",
                    ],
                },
                "descending": {
                    "type": "boolean",
                },
            },
            "additionalProperties": False,
        },
    },
    "required": ["action", "tool", "arguments"],
    "additionalProperties": False,
}

FINAL_SCHEMA = {
    "type": "object",
    "properties": {
        "cancel": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
    },
    "required": ["cancel"],
    "additionalProperties": False,
}

STAGE_LABELS = {
    "analiza_uzywania_i_cen": (
        "[1/4] Analiza ostatniego uzywania i cen"
    ),
    "analiza_umow_i_ograniczen": (
        "[2/4] Analiza umow i ograniczen"
    ),
    "optymalizacja": (
        "[3/4] Optymalizacja oszczednosci"
    ),
    "niezalezna_weryfikacja": (
        "[4/4] Niezalezna weryfikacja wyniku"
    ),
}


def run_agent(base_url, model, goal, max_steps=6):
    trace_dir = ROOT / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)

    trace_path = trace_dir / f"run_{uuid.uuid4().hex[:10]}.jsonl"

    started = time.perf_counter()

    total_input_tokens = 0
    total_output_tokens = 0
    model_calls = 0
    collection_steps = 0

    stage = "collect"
    status = "running"
    final_answer = None

    collected = {}
    completed_calls = set()
    stage_results = {}

    def as_json(value):
        return json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
        )

    def log(event):
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "external_api_cost_usd": 0,
            **event,
        }

        with trace_path.open("a", encoding="utf-8") as output:
            output.write(
                json.dumps(record, ensure_ascii=False) + "\n"
            )

    def call_key(name, arguments):
        return (
            name,
            json.dumps(
                arguments,
                sort_keys=True,
                ensure_ascii=False,
            ),
        )

    required_calls = {
        call_key(
            "read_preferences",
            {},
        ): "preferencje uzytkownika",

        call_key(
            "read_subscriptions",
            {},
        ): "subskrypcje i oceny przygotowane przez narzedzie",

        call_key(
            "sort_subscriptions",
            {"by": "last_used", "descending": True},
        ): "sortowanie od najdluzej nieuzywanych",

        call_key(
            "sort_subscriptions",
            {"by": "price", "descending": True},
        ): "sortowanie od najdrozszych",

        call_key(
            "read_contract_rules",
            {},
        ): "dostepne informacje o umowach i zwrotach",
    }

    def missing_calls():
        return [
            {
                "tool": name,
                "arguments": json.loads(arguments),
                "purpose": description,
            }
            for (name, arguments), description
            in required_calls.items()
            if (name, arguments) not in completed_calls
        ]

    def ask(messages, json_mode=False, output_schema=None):
        nonlocal total_input_tokens
        nonlocal total_output_tokens
        nonlocal model_calls

        model_calls += 1
        call_number = model_calls
        request_started = time.perf_counter()

        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0,
                "num_ctx": 16384,
                "num_predict": 512 if json_mode else 2500,
            },
        }

        if output_schema is not None:
            body["format"] = output_schema
        elif json_mode:
            body["format"] = TOOL_SCHEMA

        log({
            "event": "model_request",
            "call": call_number,
            "stage": stage,
            "json_mode": json_mode,
            "messages": messages,
            "options": body["options"],
        })

        try:
            response = requests.post(
                f"{base_url}/api/chat",
                json=body,
                timeout=(10, 900),
            )
            response.raise_for_status()

            payload = response.json()

            if not isinstance(payload, dict):
                raise RuntimeError(
                    "Nieprawidlowy format odpowiedzi serwera."
                )

            if payload.get("error"):
                raise RuntimeError(str(payload["error"]))

            message = payload.get("message")

            if not isinstance(message, dict):
                raise RuntimeError(
                    "Brak obiektu message w odpowiedzi serwera."
                )

            text = message.get("content")

            if not isinstance(text, str) or not text.strip():
                raise RuntimeError("Model zwrocil pusta odpowiedz.")

            input_tokens = payload.get("prompt_eval_count", 0) or 0
            output_tokens = payload.get("eval_count", 0) or 0

            total_input_tokens += input_tokens
            total_output_tokens += output_tokens

            log({
                "event": "model_response",
                "call": call_number,
                "stage": stage,
                "response": text,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "done_reason": payload.get("done_reason"),
                "delay_ms": round(
                    (time.perf_counter() - request_started) * 1000,
                    2,
                ),
            })

            if payload.get("done_reason") == "length":
                raise RuntimeError(
                    f"Etap {stage}: odpowiedz zostala ucieta "
                    "przez limit dlugosci. Szczegoly sa w logu."
                )

            return text.strip()

        except Exception as error:
            log({
                "event": "model_error",
                "call": call_number,
                "stage": stage,
                "error": str(error),
                "delay_ms": round(
                    (time.perf_counter() - request_started) * 1000,
                    2,
                ),
            })
            raise

    def validate_tool_decision(raw):
        decision = json.loads(raw)

        if not isinstance(decision, dict):
            raise ValueError("Decyzja musi byc obiektem JSON.")

        if set(decision) != {"action", "tool", "arguments"}:
            raise ValueError(
                "Wymagane pola: action, tool, arguments."
            )

        action = decision["action"]
        name = decision["tool"]
        arguments = decision["arguments"]

        if not isinstance(action, str):
            raise ValueError("action musi byc tekstem.")

        if not isinstance(name, str):
            raise ValueError("tool musi byc tekstem.")

        if not isinstance(arguments, dict):
            raise ValueError("arguments musi byc obiektem JSON.")

        if action == "finish":
            if name or arguments:
                raise ValueError(
                    "Dla finish wymagane tool='' i arguments={}."
                )

            if missing_calls():
                raise ValueError(
                    "Brakuje danych. Wybierz wywolanie z missing_calls."
                )

            return decision

        if action != "tool":
            raise ValueError(
                "Dozwolone akcje: tool albo finish."
            )

        allowed_arguments = {
            "read_preferences": set(),
            "read_subscriptions": set(),
            "read_contract_rules": set(),
            "sort_subscriptions": {"by", "descending"},
        }

        if name not in allowed_arguments:
            raise ValueError(f"Nieznane narzedzie: {name}")

        if set(arguments) != allowed_arguments[name]:
            raise ValueError(
                f"Nieprawidlowe argumenty dla {name}."
            )

        if name == "sort_subscriptions":
            by = arguments["by"]

            if not isinstance(by, str) or by not in {
                "last_used",
                "price",
                "name",
                "contract",
            }:
                raise ValueError(
                    "by: last_used, price, name lub contract."
                )

            if not isinstance(arguments["descending"], bool):
                raise ValueError(
                    "descending musi byc true albo false."
                )

        if call_key(name, arguments) in completed_calls:
            raise ValueError(
                "To wywolanie zostalo juz wykonane. "
                "Wybierz brakujace wywolanie."
            )

        return decision

    def save_tool_result(name, arguments, result):
        if name == "sort_subscriptions":
            direction = "desc" if arguments["descending"] else "asc"
            label = f"sort_{arguments['by']}_{direction}"
            collected[label] = result
        else:
            collected[name] = result

    def run_text_stage(stage_name, system_prompt, content):
        nonlocal stage

        stage = stage_name

        print(
            f"\n{STAGE_LABELS.get(stage_name, stage_name)}...",
            flush=True,
        )

        # Nowa rozmowa dla każdego etapu.
        answer = ask([
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": as_json(content),
            },
        ])

        stage_results[stage_name] = answer

        log({
            "event": "stage_result",
            "stage": stage_name,
            "answer": answer,
        })

        print("  Gotowe.", flush=True)

        return answer

    log({
        "event": "start",
        "model": model,
        "goal": goal,
        "max_collection_steps": max_steps,
    })

    try:
        print(
            "\n[Zbieranie] Odczyt danych i przygotowanie zestawien...",
            flush=True,
        )

        messages = [
            {
                "role": "system",
                "content": prompt_tools,
            },
            {
                "role": "user",
                "content": goal,
            },
        ]

        collection_finished = False

        for step in range(1, max_steps + 1):
            collection_steps = step
            pending = missing_calls()

            messages.append({
                "role": "user",
                "content": as_json({
                    "type": "collection_status",
                    "missing_calls": pending,
                    "instruction": (
                        "Wybierz jedno wywolanie z missing_calls."
                        if pending
                        else (
                            "Wszystkie wymagane dane sa zebrane. "
                            "Zwroc action=finish, tool='', arguments={}."
                        )
                    ),
                }),
            })

            raw = ask(messages, json_mode=True)

            messages.append({
                "role": "assistant",
                "content": raw,
            })

            try:
                decision = validate_tool_decision(raw)

            except (ValueError, TypeError, KeyError) as error:
                feedback = {
                    "type": "validation_error",
                    "error": str(error),
                    "missing_calls": missing_calls(),
                }

                log({
                    "event": "validation_error",
                    "step": step,
                    "feedback": feedback,
                })

                messages.append({
                    "role": "user",
                    "content": as_json(feedback),
                })

                continue

            log({
                "event": "validated_decision",
                "step": step,
                "decision": decision,
            })

            if decision["action"] == "finish":
                collection_finished = True
                break

            name = decision["tool"]
            arguments = decision["arguments"]

            tool_started = time.perf_counter()

            try:
                result = execute_tool(name, arguments)

            except Exception as error:
                log({
                    "event": "tool_result",
                    "step": step,
                    "tool": name,
                    "arguments": arguments,
                    "success": False,
                    "error": str(error),
                    "delay_ms": round(
                        (time.perf_counter() - tool_started) * 1000,
                        2,
                    ),
                })
                raise

            completed_calls.add(call_key(name, arguments))
            save_tool_result(name, arguments, result)

            log({
                "event": "tool_result",
                "step": step,
                "tool": name,
                "arguments": arguments,
                "success": True,
                "result": result,
                "delay_ms": round(
                    (time.perf_counter() - tool_started) * 1000,
                    2,
                ),
            })

            messages.append({
                "role": "user",
                "content": as_json({
                    "type": "tool_result",
                    "tool": name,
                    "arguments": arguments,
                    "result": result,
                }),
            })

        if not collection_finished:
            status = "step_limit"
            final_answer = (
                "Osiagnieto limit decyzji podczas zbierania danych. "
                "Analizy nie zostaly uruchomione. "
                "Szczegoly sa w logu JSONL."
            )

        else:
            print("  Dane zebrane.", flush=True)

            original_data = {
                "goal": goal,
                "subscriptions": collected["read_subscriptions"],
                "preferences": collected["read_preferences"],
                "contract_rules": collected["read_contract_rules"],
            }

            activity_analysis = run_text_stage(
                stage_name="analiza_uzywania_i_cen",
                system_prompt=prompt1,
                content={
                    **original_data,
                    "sorted_by_inactivity": collected[
                        "sort_last_used_desc"
                    ],
                    "sorted_by_price": collected[
                        "sort_price_desc"
                    ],
                },
            )

            contract_analysis = run_text_stage(
                stage_name="analiza_umow_i_ograniczen",
                system_prompt=prompt2,
                content={
                    **original_data,
                    "activity_analysis": activity_analysis,
                },
            )

            proposal = run_text_stage(
                stage_name="optymalizacja",
                system_prompt=prompt3,
                content={
                    **original_data,
                    "activity_analysis": activity_analysis,
                    "contract_analysis": contract_analysis,
                },
            )

            # Osobna rozmowa bez historii wcześniejszych analiz.
            # Weryfikator dostaje dane i propozycję do sprawdzenia.
            stage = "niezalezna_weryfikacja"

            print(
                "\n[4/4] Niezalezna weryfikacja wyniku...",
                flush=True,
            )

            verification_messages = [
                {
                    "role": "system",
                    "content": prompt4,
                },
                {
                    "role": "user",
                    "content": as_json({
                        **original_data,
                        "proposal_to_verify": proposal,
                    }),
                },
            ]

            records = original_data["subscriptions"]

            by_name = {
                record["name"]: record
                for record in records
            }

            if len(by_name) != len(records):
                raise ValueError(
                    "Dane zawieraja powtarzajace sie nazwy subskrypcji."
                )

            selected_names = None

            # Jedna dodatkowa próba, jeżeli wybór jest niepoprawny.
            for attempt in range(1, 3):
                raw = ask(
                    verification_messages,
                    output_schema=FINAL_SCHEMA,
                )

                verification_messages.append({
                    "role": "assistant",
                    "content": raw,
                })

                try:
                    selection = json.loads(raw)

                    if (
                        not isinstance(selection, dict)
                        or set(selection) != {"cancel"}
                    ):
                        raise ValueError(
                            "Wymagany obiekt z jednym polem cancel."
                        )

                    names = selection["cancel"]

                    if not isinstance(names, list) or any(
                        not isinstance(name, str)
                        for name in names
                    ):
                        raise ValueError(
                            "cancel musi byc lista nazw."
                        )

                    if len(names) != len(set(names)):
                        raise ValueError(
                            "Lista zawiera powtorzone nazwy."
                        )

                    unknown = [
                        name for name in names
                        if name not in by_name
                    ]

                    if unknown:
                        raise ValueError(
                            f"Nieznane subskrypcje: {unknown}"
                        )

                    forbidden = [
                        name
                        for name in names
                        if not by_name[name][
                            "cancellation_assessment"
                        ][
                            "eligible_under_recorded_preferences"
                        ]
                    ]

                    if forbidden:
                        raise ValueError(
                            "Wybrano uslugi, ktore musza pozostac: "
                            + ", ".join(forbidden)
                        )

                    selected_names = set(names)

                    log({
                        "event": "verified_selection",
                        "attempt": attempt,
                        "selection": selection,
                    })

                    break

                except (ValueError, TypeError, KeyError) as error:
                    log({
                        "event": "verification_validation_error",
                        "attempt": attempt,
                        "error": str(error),
                        "raw_response": raw,
                    })

                    if attempt == 2:
                        raise RuntimeError(
                            "Model nie zwrocil poprawnego wyboru "
                            f"po ponownej weryfikacji: {error}"
                        ) from error

                    verification_messages.append({
                        "role": "user",
                        "content": as_json({
                            "type": "validation_error",
                            "error": str(error),
                            "instruction": (
                                "Popraw liste cancel na podstawie "
                                "oryginalnych danych i ocen narzedzia."
                            ),
                        }),
                    })

            kept = [
                record
                for record in records
                if record["name"] not in selected_names
            ]

            cancelled = [
                record
                for record in records
                if record["name"] in selected_names
            ]

            def number(value):
                return f"{value:g}"

            lines = ["ZOSTAW"]

            for record in kept:
                assessment = record["cancellation_assessment"]

                if assessment[
                    "eligible_under_recorded_preferences"
                ]:
                    reason = (
                        "Nie wybrano do anulowania "
                        "w koncowej rekomendacji."
                    )
                else:
                    reason = " ".join(assessment["reasons"])

                lines.append(
                    f"- {record['name']} — {reason}"
                )

            if not kept:
                lines.append("- Brak.")

            lines.extend(["", "ZREZYGNUJ"])

            for record in cancelled:
                lines.append(
                    f"- {record['name']} — "
                    f"{number(record['monthly_price_pln'])} zl/mies.; "
                    f"{number(record['days_since_last_use'])} dni "
                    "od ostatniego uzycia."
                )

            if not cancelled:
                lines.append("- Brak.")

            # Sumowanie cen, nie generowanie sumy przez model.
            from decimal import Decimal

            prices = [
                Decimal(str(record["monthly_price_pln"]))
                for record in cancelled
            ]

            savings = sum(prices, Decimal("0"))

            target = Decimal(str(
                original_data["preferences"][
                    "monthly_savings_target_pln"
                ]
            ))

            expression = (
                " + ".join(number(price) for price in prices)
                if prices
                else "0"
            )

            lines.extend([
                "",
                "OSZCZEDNOSCI",
                (
                    f"{expression} = "
                    f"{number(savings)} zl/mies."
                ),
                (
                    f"Cel: {number(target)} zl/mies. — "
                    + (
                        "osiagniety."
                        if savings >= target
                        else (
                            "nieosiagniety; brakuje "
                            f"{number(target - savings)} zl/mies."
                        )
                    )
                ),
            ])

            rules = original_data["contract_rules"]

            if rules.get("additional_rules") is None:
                lines.extend([
                    "",
                    "Brak danych o zasadach zwrotu.",
                ])

            final_answer = "\n".join(lines)
            stage_results[stage] = final_answer

            log({
                "event": "stage_result",
                "stage": stage,
                "selected_to_cancel": sorted(selected_names),
                "monthly_savings_pln": str(savings),
                "answer": final_answer,
            })

            print("  Gotowe.", flush=True)
            status = "done"

    except KeyboardInterrupt:
        status = "interrupted"
        final_answer = "Przerwano dzialanie agenta."

        log({
            "event": "interrupted",
            "stage": stage,
        })

    except Exception as error:
        status = "error"
        final_answer = (
            f"Agent zatrzymany z powodu bledu: {error}"
        )

        log({
            "event": "error",
            "stage": stage,
            "error": str(error),
        })

    finally:
        log({
            "event": "summary",
            "status": status,
            "stage": stage,
            "answer": final_answer,
            "collection_steps": collection_steps,
            "model_calls": model_calls,
            "completed_tool_calls": [
                {
                    "tool": name,
                    "arguments": json.loads(arguments),
                }
                for name, arguments in sorted(completed_calls)
            ],
            "stage_results": stage_results,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_delay_ms": round(
                (time.perf_counter() - started) * 1000,
                2,
            ),
        })

    if status == "done":
        print("\n========== WYNIK KONCOWY ==========\n")
        print(final_answer)
    else:
        # Dotychczasowy main.py drukuje treść niepowodzenia.
        print(f"\nStatus: {status}")

    print(f"\nLog: {trace_path.name}")

    return final_answer