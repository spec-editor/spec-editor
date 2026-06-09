# Example: Online Bookstore

This example shows the full Spec Editor pipeline on a realistic project.

## What You'll See

```
input.md              →  [spec-editor run]  →  Structured Spec
(requirements doc)         (agents debate)       (modules, scenarios,
                                                   data models, NFRs)
                                                        ↓
                                              [verify-traceability]
                                                        ↓
                                              Coverage Report
                                              "12/15 requirements
                                               have @implements"
```

## Input

[input.md](input.md) — a typical requirements document written as a team chat.

It's intentionally messy: feature requests, non-functional requirements,
and open questions mixed together. The kind of thing a product manager
drops in Slack on Friday afternoon.

## Expected Output

After `spec-editor run`, the agents produce:

→ [Expected spec summary](expected/spec-summary.md)

Key results:
- **5 modules** decomposed from the requirements (Catalog, Cart, Checkout, Accounts, Admin)
- **8 user stories** with acceptance criteria
- **4 data entities** (Book, Order, User, CartItem)
- **4 non-functional requirements** (performance, scalability, PCI-DSS, GDPR)
- **Full traceability**: every spec element traced back to `input.md`
- **Coverage gaps** identified (3 unimplemented requirements)

## Try It Yourself

```bash
# 1. Install
git clone https://github.com/spec-editor/spec-editor
cd spec-editor
pip install -e ".[dev]"

# 2. Set your API key
echo "DEEPSEEK_API_KEY=sk-..." > .env

# 3. Create project from this example
spec-editor init ./bookstore-demo
cp examples/bookstore/input.md bookstore-demo/source/

# 4. Run the agents
cd bookstore-demo
spec-editor run

# 5. See what they built
spec-editor status
spec-editor validate

# 6. After writing code with @implements annotations:
spec-editor verify-traceability -p . -c ../src -l python
```

## What Makes This Cool

- **Input**: one markdown file with unstructured chat-style requirements
- **Process**: two AI agents debate and refine over multiple rounds
- **Output**: structured, traceable, version-controlled specification
- **Traceability**: every requirement knows where it came from (`derived_from`)
- **Cost**: ~$0.05 with DeepSeek (100x cheaper than GPT-4)
