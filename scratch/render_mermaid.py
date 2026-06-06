import urllib.request
import json
import base64
import os

diagrams = {
    "circuit_breaker.png": """stateDiagram-v2
    [*] --> CLOSED : Initialize
    CLOSED --> OPEN : N consecutive failures
    OPEN --> HALF_OPEN : Cooldown window expires
    HALF_OPEN --> CLOSED : Probe success (reset)
    HALF_OPEN --> OPEN : Probe failure (restart cooldown)""",
    
    "request_flow.png": """sequenceDiagram
    autonumber
    actor User as Client / API
    participant O as Orchestrator
    participant CG as CostGovernor
    participant R as FailoverRouter
    participant LC as LLMClient
    participant LG as LangGraph Engine
    participant DB as Postgres DB

    User->>O: run(topic, run_id, user_id)
    O->>CG: Initialize (Seeds spend from DB)
    DB-->>CG: Return current run & user spend
    O->>LC: Instantiate LLMClient(cost_governor)
    O->>LG: graph.ainvoke(initial_state)

    loop Every Node Execution
        LG->>LC: complete(messages)
        LC->>CG: pre_check(base_model)
        CG-->>LC: Return effective_model
        LC->>R: call(messages, model=effective_model)
        
        loop Retry Loop & Failover
            R->>R: Attempt LLM API call
        end
        
        R-->>LC: Return response + TokenUsage
        LC->>CG: record_usage(usage, provider, model)
        CG->>DB: Insert CostLedgerEntry
        LC-->>LG: Return Text response
        LG->>DB: Persist Checkpoint (LangGraph)
    end
    
    LG-->>O: Final Graph State
    O-->>User: Result""",
    
    "decision_flow.png": """graph TD
    A[LLM Call Initiated] --> B[Retrieve Run & User Spend]
    B --> C{Run Spend >= Run Ceiling OR<br/>User Spend >= User Ceiling?}
    C -- Yes --> D[Raise BudgetExceededError<br/>Hard Halt]
    C -- No --> E{Run Spend >= Degradation Threshold OR<br/>User Spend >= Degradation Threshold?}
    E -- Yes --> F[Look up Model in model_downgrade_map]
    F --> G{Cheaper Mapped Model Available?}
    G -- Yes --> H[Downgrade Model Silently]
    G -- No --> I[Use Original Model]
    E -- No --> I
    H --> J[Proceed with LLM Call]
    I --> J"""
}

os.makedirs("data/diagrams", exist_ok=True)

for filename, code in diagrams.items():
    # Construct the JSON payload for mermaid.ink
    payload = {
        "code": code,
        "mermaid": {"theme": "default"}
    }
    json_bytes = json.dumps(payload).encode("utf-8")
    b64_str = base64.urlsafe_b64encode(json_bytes).decode("utf-8")
    
    url = f"https://mermaid.ink/img/{b64_str}"
    
    dest_path = os.path.join("data/diagrams", filename)
    print(f"Downloading {url} to {dest_path}...")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            with open(dest_path, "wb") as f:
                f.write(response.read())
        print(f"Successfully saved {filename}")
    except Exception as e:
        print(f"Failed to download {filename}: {e}")
