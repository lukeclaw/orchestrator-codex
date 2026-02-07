#!/usr/bin/env python3
"""
Test script to verify we can reuse Claude Code's API token from macOS Keychain.
"""

import subprocess
import sys

def get_claude_code_token() -> str | None:
    """Read the API token that Claude Code stored in macOS Keychain"""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code", "-w"],
            capture_output=True,
            text=True,
            check=True
        )
        token = result.stdout.strip()
        if token.startswith("sk-ant-"):
            return token
        return None
    except subprocess.CalledProcessError:
        return None


def test_token(token: str) -> bool:
    """Test the token by making a simple API call using curl"""
    import json
    
    print("📡 Making test API call with curl...")
    
    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 50,
        "messages": [{
            "role": "user",
            "content": "Say 'Token works!' in exactly 2 words."
        }]
    })
    
    result = subprocess.run(
        [
            "curl", "-s",
            "https://api.anthropic.com/v1/messages",
            "-H", f"x-api-key: {token}",
            "-H", "anthropic-version: 2023-06-01",
            "-H", "content-type: application/json",
            "-d", payload
        ],
        capture_output=True,
        text=True
    )
    
    try:
        response = json.loads(result.stdout)
        
        if "error" in response:
            print(f"❌ API Error: {response['error'].get('message', response['error'])}")
            return False
        
        if "content" in response and len(response["content"]) > 0:
            reply = response["content"][0].get("text", "")
            print(f"✅ API Response: {reply}")
            return True
        
        print(f"❌ Unexpected response: {result.stdout[:200]}")
        return False
        
    except json.JSONDecodeError:
        print(f"❌ Invalid JSON response: {result.stdout[:200]}")
        return False


def main():
    print("🔐 Testing Claude Code Token Reuse\n")
    print("=" * 50)
    
    # Step 1: Read token from Keychain
    print("\n1️⃣  Reading token from macOS Keychain...")
    token = get_claude_code_token()
    
    if not token:
        print("❌ No token found in Keychain.")
        print("   Make sure you've logged into Claude Code first:")
        print("   $ claude")
        print("   Then run /login and select 'Console account'")
        sys.exit(1)
    
    print(f"✅ Found token: {token[:20]}...{token[-10:]}")
    print(f"   Length: {len(token)} characters")
    
    # Step 2: Test the token
    print("\n2️⃣  Testing token with Anthropic API...")
    success = test_token(token)
    
    print("\n" + "=" * 50)
    if success:
        print("🎉 SUCCESS! Token is valid and working.")
        print("\n   You can use this token in your orchestrator:")
        print("   ```python")
        print("   import anthropic")
        print("   client = anthropic.Anthropic(api_key=get_claude_code_token())")
        print("   ```")
    else:
        print("💔 Token validation failed.")
        print("   Try re-authenticating: claude /login")
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
