"""DeepSeek for the video factory, through its Anthropic-compatible endpoint.

The API key is never stored in this repo. It is read at run time from Windows Credential
Manager (service "ExplainerStudio", user "deepseek_api_key"), the same entry Video Studio
and Explainer Studio use. This module only reads it.

    from deepseek_client import ask
    text = ask("Write a 20-word narration line about ...")
    text = ask("What happened in ... this week?", search=True)

usage: python deepseek_client.py "<prompt>" [--search]
needs: pip install keyring anthropic
"""
import datetime
import sys

BASE_URL = "https://api.deepseek.com/anthropic"
MODEL = "deepseek-v4-pro"
SEARCH_MODEL = "deepseek-flash"   # 2026-11-22: DeepSeek disabled its web-search tool on deepseek-v4-pro ("use deepseek-flash")
VAULT = ("ExplainerStudio", "deepseek_api_key")
WEB_SEARCH = {"type": "deepseek_web_search", "name": "deepseek_web_search"}


def client():
    import anthropic
    import keyring
    key = keyring.get_password(*VAULT)
    if not key:
        raise RuntimeError("DeepSeek key not found in Windows Credential Manager "
                           f"(service {VAULT[0]!r}, user {VAULT[1]!r})")
    return anthropic.Anthropic(api_key=key, base_url=BASE_URL)


def ask(prompt, system=None, search=False, max_tokens=2000, model=None):
    """One message to DeepSeek; returns the reply text.
    search=True adds DeepSeek's own web search. tool_choice is never set (DeepSeek returns 400),
    and today's date always goes in the system prompt so searches use the right year."""
    today = datetime.date.today().isoformat()
    sys_text = f"Today's date is {today}." + (f"\n\n{system}" if system else "")
    kw = {"tools": [WEB_SEARCH]} if search else {}
    model = model or (SEARCH_MODEL if search else MODEL)
    r = client().messages.create(model=model, max_tokens=max_tokens, system=sys_text,
                                 messages=[{"role": "user", "content": prompt}], **kw)
    return "".join(getattr(b, "text", "") for b in r.content if getattr(b, "type", "") == "text").strip()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--search"]
    if not args:
        sys.exit(__doc__)
    sys.stdout.reconfigure(encoding="utf-8")   # replies carry characters the Windows console code page lacks
    print(ask(" ".join(args), search="--search" in sys.argv))
