import os
import sys

from browser_sender import BrowserSender

ACCOUNT_NUMBERS = range(1, 11)


def log(message):
    print(f"[login-test] {message}", flush=True)


def run_login_test(environ):
    accounts = []
    missing = []
    for number in ACCOUNT_NUMBERS:
        username = (environ.get(f"SITE_USERNAME_{number}") or "").strip()
        password = environ.get(f"SITE_PASSWORD_{number}") or ""
        if not username:
            missing.append(f"SITE_USERNAME_{number}")
        if not password:
            missing.append(f"SITE_PASSWORD_{number}")
        accounts.append((number, username, password))

    if missing:
        log("تعذر بدء الاختبار؛ أسرار مفقودة: " + ", ".join(missing))
        return 2

    successful = 0
    for number, username, password in accounts:
        log(f"بدء اختبار الدخول للحساب {number}")
        try:
            with BrowserSender(username, password) as sender:
                logged_in = sender.verify_login()
        except Exception as error:
            log(f"الحساب {number}: تعذر التحقق ({type(error).__name__})")
            logged_in = False

        if logged_in:
            successful += 1
            log(f"الحساب {number}: دخول ناجح")
        else:
            log(f"الحساب {number}: فشل الدخول")

    failed = len(accounts) - successful
    log(f"الملخص: نجح {successful} من 10، وفشل أو تعذر التحقق من {failed}.")
    return 0 if failed == 0 else 1


def main():
    return run_login_test(os.environ)


if __name__ == "__main__":
    sys.exit(main())
