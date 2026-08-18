def log_token_usage(response, label="LLM Call"):
    try:
        msg = response["messages"][-1]

        metadata = getattr(msg, "response_metadata", {}) or {}
        usage = (
            metadata.get("token_usage")
            or metadata.get("usage")
            or metadata.get("usage_metadata")
            or getattr(msg, "usage_metadata", None)  # Claude (Anthropic) stores usage here
        )

        if usage:
            input_tokens = usage.get("input_tokens") or usage.get("prompt_tokens")
            output_tokens = usage.get("output_tokens") or usage.get("completion_tokens")
            total_tokens = usage.get("total_tokens")

            print(f"\n[{label}] Token Usage:")
            print(f"   Input Tokens: {input_tokens}")
            print(f"   Output Tokens: {output_tokens}")
            print(f"   Total Tokens: {total_tokens}\n")
        else:
            print(f"\n[{label}] No token usage found in metadata\n")

    except Exception as e:
        print(f"\nToken logging failed: {str(e)}\n")