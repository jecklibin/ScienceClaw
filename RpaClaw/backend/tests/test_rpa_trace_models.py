from backend.rpa.trace_models import (
    RPAAcceptedTrace,
    RPAAIExecution,
    RPAPageState,
    RPARuntimeResults,
    RPATraceType,
)


def test_ai_operation_trace_serializes_execution_and_page_state():
    trace = RPAAcceptedTrace(
        trace_id="trace-1",
        trace_type=RPATraceType.AI_OPERATION,
        source="ai",
        user_instruction="open the project with the highest star count",
        before_page=RPAPageState(url="https://github.com/trending", title="Trending"),
        after_page=RPAPageState(url="https://github.com/owner/repo", title="owner/repo"),
        ai_execution=RPAAIExecution(
            language="python",
            code="async def run(page, results):\n    return {'url': page.url}",
            output={"selected_project": {"url": "https://github.com/owner/repo"}},
        ),
        validation={"status": "ok"},
        signals={"navigation": {"url": "https://github.com/owner/repo"}},
        accepted=True,
    )

    payload = trace.model_dump()

    assert payload["trace_type"] == "ai_operation"
    assert payload["before_page"]["url"] == "https://github.com/trending"
    assert payload["ai_execution"]["language"] == "python"
    assert payload["validation"]["status"] == "ok"
    assert payload["signals"]["navigation"]["url"] == "https://github.com/owner/repo"
    assert payload["accepted"] is True


def test_trace_preserves_dynamic_binding_metadata():
    input_bindings = {
        "invoice_number": {
            "source": "user_param",
            "default": "INV-001",
            "classification": "user_param",
        }
    }
    output_bindings = {
        "supplier_name": {
            "source": "trace_output",
            "path": "supplier.name",
            "classification": "derived_data",
        }
    }
    postcondition = {
        "kind": "table_row_exists",
        "table_headers": ["Invoice", "Status"],
        "key": {"Invoice": "{{invoice_number}}"},
        "expect": {"Status": "Submitted"},
    }

    trace = RPAAcceptedTrace(
        trace_type=RPATraceType.AI_OPERATION,
        input_bindings=input_bindings,
        output_bindings=output_bindings,
        postcondition=postcondition,
    )

    payload = trace.model_dump()

    assert payload["input_bindings"] == input_bindings
    assert payload["output_bindings"] == output_bindings
    assert payload["postcondition"] == postcondition


def test_runtime_results_resolves_dotted_refs_and_list_indexes():
    results = RPARuntimeResults(
        values={
            "customer_info": {
                "name": "Alice Zhang",
                "emails": ["alice@example.com"],
            }
        }
    )

    assert results.resolve_ref("customer_info.name") == "Alice Zhang"
    assert results.resolve_ref("customer_info.emails.0") == "alice@example.com"
    assert results.find_value_refs("Alice Zhang") == ["customer_info.name"]

