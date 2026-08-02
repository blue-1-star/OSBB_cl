import sqlite3
c = sqlite3.connect(r'G:\Programming\OSBB_cl\Data\db\osbb_test.db')
print(list(c.execute("SELECT id, telegram_user_id, apartment_number, status FROM resident_accounts WHERE telegram_user_id = '962600170'")))
print(list(c.execute("SELECT id, status FROM apartment_link_requests WHERE id = 2229")))
