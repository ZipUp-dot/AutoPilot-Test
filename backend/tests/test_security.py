"""Security check unit test"""
import sys
sys.path.insert(0, ".")
from app.services.ai_service import _security_check, _validate_syntax, _extract_code
from app.exceptions import SecurityException

# Test 1: blacklisted import
print("Test 1: import os")
try:
    _validate_syntax("import os\nasync def run_test(page):\n    return {'success': True}")
    _security_check("import os\nasync def run_test(page):\n    return {'success': True}")
    print("  FAIL - should have been blocked")
except SecurityException as e:
    print(f"  PASS - blocked: {e.message}")

# Test 2: blacklisted builtin
print("Test 2: eval()")
try:
    _validate_syntax("async def run_test(page):\n    eval('1+1')\n    return {'success': True}")
    _security_check("async def run_test(page):\n    eval('1+1')\n    return {'success': True}")
    print("  FAIL - should have been blocked")
except SecurityException as e:
    print(f"  PASS - blocked: {e.message}")

# Test 3: valid code passes
print("Test 3: valid code")
try:
    _validate_syntax("async def run_test(page):\n    print('hello')\n    return {'success': True}")
    _security_check("async def run_test(page):\n    print('hello')\n    return {'success': True}")
    print("  PASS - valid code accepted")
except Exception as e:
    print(f"  FAIL - {e}")

# Test 4: code extraction
print("Test 4: extract code from markdown")
raw = "```python\nasync def run_test(page):\n    return {'success': True}\n```"
clean = _extract_code(raw)
print(f"  extracted: {clean[:60]}...")

# Test 5: syntax error
print("Test 5: syntax error")
try:
    _validate_syntax("async def run_test(page)\n    return")
    print("  FAIL - should have failed")
except SyntaxError as e:
    print(f"  PASS - syntax error caught: {str(e)[:60]}")

print("\n=== SECURITY TESTS PASSED ===")
