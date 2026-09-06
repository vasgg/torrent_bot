import os

# Never use the real bot credentials in tests.
os.environ["TOKEN"] = "123456:TEST_TOKEN"
os.environ["ADMIN_IDS"] = "[123]"
os.environ["UPTIME_TIMEZONE"] = "Asia/Tbilisi"
