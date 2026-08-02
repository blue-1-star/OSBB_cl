import sqlite3
c = sqlite3.connect(r'G:\Programming\OSBB_cl\Data\db\osbb_test.db')
print('Всего строк в apartment_link_requests:', c.execute("SELECT COUNT(*) FROM apartment_link_requests").fetchone()[0])
for row in c.execute("SELECT id, telegram_user_id, requested_apartment_number, status FROM apartment_link_requests"):
    print(row)
