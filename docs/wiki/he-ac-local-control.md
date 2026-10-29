# שליטה מקומית במזגן מיצובישי (MAC-577IF-E) - מדריך צעד אחר צעד

המדריך מפעיל ECHONET Lite - פרוטוקול שליטה מקומית שמובנה בקושחה של המתאם אבל
כבוי מחוץ ליפן/אוסטרליה. אחרי ההפעלה אפשר לשלוט במזגן מהרשת הביתית בלי ענן,
ולחבר אותו ל-Apple HomeKit דרך גשר מקומי. MELCloud Home ממשיך לעבוד במקביל.

**לפני שמתחילים**

- מחשב לינוקס (או מק) שמחובר **לאותה רשת Wi-Fi/ראוטר** כמו המתאם.
- ההפעלה לא רשמית אבל הפיכה לגמרי (סעיף 9 - כיבוי).
- הכל קורה ברשת הביתית בלבד; שום דבר לא נשלח לשום שרת שלנו.

## 1. מוצאים את כתובת ה-IP של המתאם

נכנסים לממשק הניהול של הראוטר לרשימת המכשירים המחוברים (DHCP clients) ומחפשים
את מתאם המיצובישי (לפעמים בשם MEL... או לפי כתובת ה-MAC שעל המדבקה של המתאם).
רושמים את הכתובת. מכאן והלאה נכתוב `192.168.1.50` - להחליף בכתובת האמיתית.

## 2. בודקים שהמתאם חי ברשת

```bash
ping -c 3 192.168.1.50
```

אמורים לקבל תשובות. אם לא - הכתובת שגויה או שהמחשב לא באותה רשת.

## 3. בודיקה לפני: מה המצב הנוכחי? (קריאה בלבד, לא משנה כלום)

ECHONET Lite עונה ב-UDP 3610. שולחים בקשת קריאה ובודקים אם יש תשובה:

```bash
python3 - <<'PY'
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(3)
s.sendto(bytes.fromhex('1081000105ff0101300162018000'), ('192.168.1.50', 3610))
try:
    data, addr = s.recvfrom(4096)
    print('ECHONET Lite כבר פעיל! תשובה:', data.hex())
except socket.timeout:
    print('אין תשובה - ECHONET Lite כבוי (מצב צפוי לפני הפעלה)')
PY
```

הבתים בבקשה הם מסגרת ECHONET Lite תקנית: בקשת Get של "מצב פעולה" (EPC 0x80)
ממחלקת "מזגן ביתי" (0x0130). אם יצא "כבר פעיל" - אין צורך בהפעלה, מדלגים לסעיף 6.

## 4. מפעילים

```bash
curl -H 'Content-Type: text/xml' -d '<?xml version="1.0" encoding="UTF-8"?><ESV>7WVvmfhMYzGVi70nyFhmKEy9Jo3Hg3994vi9y1kEgDFWd/1ch9RWDUgY4HgsvMHFvP93fQ30AvEJCNcd0GTwPID0F8V5eyMVj/qAQCXFqYrRtJh8MIpm2/h7jZ2SsPj0</ESV>' "http://192.168.1.50/smart"
```

- לא צריך שם משתמש או סיסמה - נקודת `/smart` היא היחידה הפתוחה במתאם.
- הבלוק הארוך הוא פקודת "הפעל ECHONET" מוצפנת במפתח היצרן (זהה בכל המתאמים
  שנבדקו בקהילה), בדיוק כפי שפורסם במקור.
- תשובה תקינה: `<?xml version="1.0" encoding="UTF-8"?><ESV>...טקסט מוצפן...</ESV>`.
- Timeout או שגיאת חיבור: לחזור לסעיפים 1-2.

## 5. מאמתים שהופעל

מריצים שוב את הבדיקה מסעיף 3. הפעם אמורה להתקבל תשובה ("ECHONET Lite כבר פעיל").

אם הפקודה בסעיף 4 החזירה XML תקין אבל אין תשובה ב-3610: לבדוק שאין הפרדת
רשתות/VLAN או "client isolation" בנקודת הגישה, ושאין חומת אש שחוסמת UDP 3610.

## 6. חיבור ל-Apple HomeKit

למתאם עצמו אין HomeKit ואין Matter, אז החיבור דרך גשר שדולק תמיד ברשת הביתית:

**אופציה א' - Homebridge (הכי ישיר ל-HomeKit):**

1. מתקינים Homebridge 2 על מחשב/רספברי שדולק תמיד (https://homebridge.io).
2. מתקינים את התוסף homebridge-echonet-hvac
   (https://github.com/karlvr/homebridge-echonet-hvac) - בנוי במיוחד למתאמי
   מיצובישי עם ECHONET, עובד מקומית לגמרי, חושף את המזגן כ-HeaterCooler עם
   הפעלה/מצב/טמפרטורה/מהירות מאוורר.
3. בהגדרות התוסף מוסיפים את המזגן (discovery אוטומטי, או IP ידני).
4. באפליקציית Home באייפון: + > Add Accessory > סריקת ה-QR של ה-Homebridge.

**אופציה ב' - Home Assistant:**

מתקינים את האינטגרציה ECHONET Lite
(https://github.com/scottyphillips/echonetlite_homeassistant), ואז את
HomeKit Bridge המובנה שחושף ישויות climate ל-Apple Home
(https://www.home-assistant.io/integrations/homekit/).

## 7. אוטומציית "כשאני מגיע הביתה"

דרך אפליקציית Home (אחרי סעיף 6):

1. Home > + > Add Automation > People Arrive.
2. בוחרים מי מגיע, מיקום (הבית) ושעות > Next.
3. בוחרים את המזגן > Next > לחיצה ארוכה על האביזר לקביעת מצב (למשל Cool, 24°) > Done.

דורש **home hub** (Apple TV או HomePod) ו-Share My Location דולק באייפון:
Settings > שם > Find My > Share My Location. בלי hub האוטומציה לא תרוץ.
(מקור: https://support.apple.com/en-us/102313)

חלופה בלי hub: Shortcuts > Automation > + > Arrive > בוחרים את הבית > Run
Immediately > פעולת Control Home שמפעילה את המזגן. רץ על האייפון עצמו ועובד
ברשת הביתית בלי hub, אבל עלול להשפע רק אחרי שהטלפון מתחבר ל-Wi-Fi.

## 8. מומלץ: IP קבוע למתאם

בראוטר, משריינים כתובת קבועה למתאם (DHCP reservation לפי MAC) כדי שהכתובת לא
תשתנה אחרי הפסקת חשמל.

## 9. כיבוי (חזרה למצב הקודם)

```bash
curl -H 'Content-Type: text/xml' -d '<?xml version="1.0" encoding="UTF-8"?><ESV>7WVvmfhMYzGVi70nyFhmKEy9Jo3Hg3994vi9y1kEgDFWd/1ch9RWDUgY4HgsvMHFrMnvUyB5uOhckVDUeRqtaYVBoGRCoB5MtcGSsQRkYZRk+cUWdicHToe7pj8/OKss</ESV>' "http://192.168.1.50/smart"
```

אותה פקודה בדיוק עם ECHONET OFF (הפורמט אומת מול פקודת ההפעלה הידועה והמקור
ב-melsmart של אותו חוקר). אימות: הבדיקה בסעיף 3 תחזור להחזיר "אין תשובה".

## 10. גרסת קושחה ועדכונים

- השיטה אומתה בקהילה על גרסת application **37.00 ומעלה** (בישנות יותר לא נבדק).
  הגרסה מופיעה בפרטי המכשיר באפליקציית MELCloud Home.
- מיצובישי מעדכנת קושחה של המתאם **דרך MELCloud** (עדכון OTA; המדריך הרשמי
  מזכיר "firmware update in progress" כסיבה לניתוק זמני).
- נכון לאוקטובר 2026 **אין דיווחים** על עדכון שכיבה את ECHONET אצל משתמשי
  השיטה (פורסמה באוגוסט 2025, משתמשים באירופה מריצים במקביל ל-MELCloud).
- אם עדכון עתידי יכבה - פשוט מריצים שוב את סעיף 4. חסימת המתאם מהאינטרנט
  מונעת עדכונים אבל גם מכבה את MELCloud Home.

## כלי עזר (לא חובה)

במקום ה-curl וקטעי ה-python יש כלי שורת פקודה בריפו הזה:

```bash
pipx install git+https://github.com/reihlinstinct/mitsubishi-echonet.git
mitsubishi-echonet status  --ip 192.168.1.50   # בדיקה (קריאה בלבד)
mitsubishi-echonet enable  --ip 192.168.1.50   # הפעלה
mitsubishi-echonet disable --ip 192.168.1.50   # כיבוי
mitsubishi-echonet discover                    # מציאת מכשירי ECHONET ברשת
mitsubishi-echonet capabilities --ip 192.168.1.50   # יכולות המתאם, כולל טיימרים (קריאה בלבד)
```

ובנוסף סקריפט עצמאי להדלקה/כיבוי ישירים של המזגן דרך /smart (בלי ECHONET, בלי
תלויות - רק python3, עובד גם בלי שהופעל ECHONET):

```bash
python3 scripts/ac_smart.py --ip 192.168.1.50 status   # מצב מלא (קריאה בלבד)
python3 scripts/ac_smart.py --ip 192.168.1.50 on       # הדלקה (שומר מצב/טמפ' אחרונים)
python3 scripts/ac_smart.py --ip 192.168.1.50 off      # כיבוי
```

## הרצה בקונטיינר (בלי להתקין כלום על המחשב)

אם לא רוצים להתקין פייתון/חבילות על המחשב, הכלי רץ בקונטיינר (docker או podman -
אותן פקודות בדיוק):

```bash
git clone https://github.com/reihlinstinct/mitsubishi-echonet.git
cd mitsubishi-echonet
docker build -t mitsubishi-echonet .

docker run --rm --network host mitsubishi-echonet status  --ip 192.168.1.50
docker run --rm --network host mitsubishi-echonet enable  --ip 192.168.1.50
docker run --rm --network host mitsubishi-echonet disable --ip 192.168.1.50
docker run --rm --network host mitsubishi-echonet discover
docker run --rm --network host mitsubishi-echonet capabilities --ip 192.168.1.50
```

- `--network host` נחוץ בלינוקס כדי שבדיקת ה-UDP (וסריקת ה-multicast של
  discover) יגיעו למתאם; בלעדיו status ו-discover לא יקבלו תשובות.
- האימג' מינימלי (Alpine, נעוץ ב-digest), רץ כמשתמש לא-רוט, ולא מתקין אף חבילה
  חיצונית - רק ספריית התקן של פייתון.
- פקודות ה-curl וה-python מהסעיפים הקודמים לא צריכות קונטיינר בכלל - הן רצות
  ישירות במערכת.

## אזהרות

- ל-ECHONET Lite **אין אימות ואין הצפנה** - כל מכשיר ברשת יכול לשלוט במזגן.
  מתאים לרשת ביתית אמינה בלבד (גם בתיעוד הרשמי של מיצובישי ניו זילנד).
- טמפרטורה במעלות שלמות בלבד (מגבלת הפרוטוקול).
- המתאם מדווח ל-MELCloud שהופעל ECHONET. MELCloud Home ממשיכה לעבוד במקביל.

## מקורות

- שיטת ההפעלה וה-payload: https://github.com/scottyphillips/echonetlite_homeassistant/issues/226
- מחקר הפרוטוקול: https://github.com/ncaunt/meldec/issues/2
- פקודות enable/disable (melsmart): https://github.com/mafredri/meltools
- התוסף ל-Homebridge: https://github.com/karlvr/homebridge-echonet-hvac
- אינטגרציית Home Assistant: https://github.com/scottyphillips/echonetlite_homeassistant
- מיצובישי NZ (רשמי): https://www.mitsubishi-electric.co.nz/wifi/learn_echonet.aspx
- מדריך MELCloud Home (רשמי): https://media.crystallize.com/mitsubishi-prod_cb88776/25/11/28/1/melcloud-home-user-manual-v1-1-1.pdf
- אוטומציות Home (רשמי): https://support.apple.com/en-us/102313
