<!-- wiki-lang: code=fr; name=Français; dir=ltr; status=machine-draft; source=he-ac-local-control -->
# Contrôle local d'un climatiseur Mitsubishi (MAC-577IF-E) - guide pas à pas

> **Brouillon de traduction automatique, à relire par un locuteur natif.** La
> version de référence est le [guide en hébreu](he-ac-local-control) ; en cas de
> différence, c'est elle qui fait foi.

Ce guide active ECHONET Lite, un protocole de contrôle local intégré au
micrologiciel de l'adaptateur mais désactivé hors du Japon et de l'Australie.
Une fois activé, le climatiseur se pilote depuis le réseau domestique, sans
cloud, et peut être relié à Apple HomeKit par une passerelle locale. MELCloud
Home continue de fonctionner en parallèle.

**Avant de commencer**

- Un ordinateur Linux (ou Mac) connecté **au même réseau Wi-Fi/routeur** que l'adaptateur.
- L'activation n'est pas officielle, mais elle est entièrement réversible (section 9 - désactivation).
- Tout se passe uniquement sur le réseau domestique ; rien n'est envoyé à un serveur à nous.

## 1. Trouver l'adresse IP de l'adaptateur

Ouvrez l'interface d'administration du routeur, à la liste des appareils
connectés (clients DHCP), et cherchez l'adaptateur Mitsubishi (parfois nommé
MEL..., ou repérable par l'adresse MAC inscrite sur son étiquette). Notez
l'adresse. Dans la suite, nous écrivons `192.168.1.50` : remplacez-la par
l'adresse réelle.

## 2. Vérifier que l'adaptateur répond sur le réseau

```bash
ping -c 3 192.168.1.50
```

Vous devez recevoir des réponses. Sinon, l'adresse est fausse ou l'ordinateur
n'est pas sur le même réseau.

## 3. Vérification préalable : quel est l'état actuel ? (lecture seule, ne modifie rien)

ECHONET Lite répond sur UDP 3610. On envoie une requête de lecture et on regarde
s'il y a une réponse :

```bash
python3 - <<'PY'
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(3)
s.sendto(bytes.fromhex('1081000105ff0101300162018000'), ('192.168.1.50', 3610))
try:
    data, addr = s.recvfrom(4096)
    print('ECHONET Lite déjà actif ! Réponse :', data.hex())
except socket.timeout:
    print('Pas de réponse - ECHONET Lite désactivé (état attendu avant activation)')
PY
```

Les octets de la requête forment une trame ECHONET Lite standard : une requête
Get de l'« état de fonctionnement » (EPC 0x80) de la classe « climatiseur
domestique » (0x0130). Si le message « déjà actif » s'affiche, l'activation est
inutile : passez à la section 6.

## 4. Activer

```bash
curl -H 'Content-Type: text/xml' -d '<?xml version="1.0" encoding="UTF-8"?><ESV>7WVvmfhMYzGVi70nyFhmKEy9Jo3Hg3994vi9y1kEgDFWd/1ch9RWDUgY4HgsvMHFvP93fQ30AvEJCNcd0GTwPID0F8V5eyMVj/qAQCXFqYrRtJh8MIpm2/h7jZ2SsPj0</ESV>' "http://192.168.1.50/smart"
```

- Aucun nom d'utilisateur ni mot de passe : le point d'accès `/smart` est le seul ouvert sur l'adaptateur.
- Le long bloc est la commande « activer ECHONET », chiffrée avec la clé du
  fabricant (identique sur tous les adaptateurs testés par la communauté),
  exactement telle qu'elle a été publiée à l'origine.
- Réponse correcte : `<?xml version="1.0" encoding="UTF-8"?><ESV>...texte chiffré...</ESV>`.
- Délai dépassé ou erreur de connexion : revenez aux sections 1-2.

## 5. Vérifier l'activation

Relancez la vérification de la section 3. Cette fois, vous devez obtenir une
réponse (« ECHONET Lite déjà actif »).

Si la commande de la section 4 a renvoyé un XML correct mais qu'il n'y a pas de
réponse sur 3610 : vérifiez qu'il n'y a pas de séparation de réseaux/VLAN ni
d'« isolation des clients » sur le point d'accès, et qu'aucun pare-feu ne bloque
UDP 3610.

## 6. Connexion à Apple HomeKit

L'adaptateur lui-même n'a ni HomeKit ni Matter ; la connexion passe donc par une
passerelle allumée en permanence sur le réseau domestique :

**Option A - Homebridge (le plus direct pour HomeKit) :**

1. Installez Homebridge 2 sur un ordinateur ou un Raspberry Pi toujours allumé (https://homebridge.io).
2. Installez le plugin homebridge-echonet-hvac
   (https://github.com/karlvr/homebridge-echonet-hvac) : conçu spécialement pour
   les adaptateurs Mitsubishi avec ECHONET, il fonctionne entièrement en local et
   présente le climatiseur comme un HeaterCooler avec marche/mode/température/vitesse
   du ventilateur.
3. Dans les réglages du plugin, ajoutez le climatiseur (découverte automatique, ou IP saisie à la main).
4. Dans l'app Maison (Home) de l'iPhone : + > Add Accessory (Ajouter un accessoire) > scannez le QR code de Homebridge.

**Option B - Home Assistant :**

Installez l'intégration ECHONET Lite
(https://github.com/scottyphillips/echonetlite_homeassistant), puis la passerelle
HomeKit Bridge intégrée, qui expose les entités climate à Apple Maison
(https://www.home-assistant.io/integrations/homekit/).

## 7. Automatisation « quand j'arrive à la maison »

Dans l'app Maison (après la section 6) :

1. Home > + > Add Automation > People Arrive (les libellés de l'app sont donnés en anglais, comme dans la version de référence).
2. Choisissez qui arrive, le lieu (la maison) et les horaires > Next.
3. Choisissez le climatiseur > Next > appui long sur l'accessoire pour régler l'état (par exemple Cool, 24°) > Done.

Nécessite un **concentrateur domestique** (Apple TV ou HomePod) et le partage de
position activé sur l'iPhone : Settings (Réglages) > votre nom > Find My
(Localiser) > Share My Location (Partager ma position). Sans concentrateur,
l'automatisation ne s'exécutera pas.
(Source : https://support.apple.com/en-us/102313)

Alternative sans concentrateur : Shortcuts (Raccourcis) > Automation > + > Arrive
> choisissez la maison > Run Immediately > action Control Home qui allume le
climatiseur. Elle s'exécute sur l'iPhone lui-même et fonctionne sur le réseau
domestique sans concentrateur, mais elle peut ne se déclencher qu'une fois le
téléphone connecté au Wi-Fi.

## 8. Recommandé : une IP fixe pour l'adaptateur

Dans le routeur, réservez une adresse fixe pour l'adaptateur (réservation DHCP
par adresse MAC) afin que l'adresse ne change pas après une coupure de courant.

## 9. Désactivation (retour à l'état précédent)

```bash
curl -H 'Content-Type: text/xml' -d '<?xml version="1.0" encoding="UTF-8"?><ESV>7WVvmfhMYzGVi70nyFhmKEy9Jo3Hg3994vi9y1kEgDFWd/1ch9RWDUgY4HgsvMHFrMnvUyB5uOhckVDUeRqtaYVBoGRCoB5MtcGSsQRkYZRk+cUWdicHToe7pj8/OKss</ESV>' "http://192.168.1.50/smart"
```

C'est exactement la même commande, avec ECHONET OFF (le format a été vérifié
par rapport à la commande d'activation connue et à la source dans melsmart, du
même chercheur). Vérification : le test de la section 3 doit de nouveau
renvoyer « Pas de réponse ».

## 10. Version du micrologiciel et mises à jour

- La méthode a été vérifiée par la communauté sur la version d'application
  **37.00 et supérieures** (non testée sur les plus anciennes). La version
  figure dans les détails de l'appareil dans l'app MELCloud Home.
- Mitsubishi met à jour le micrologiciel de l'adaptateur **via MELCloud** (mise
  à jour OTA ; le guide officiel cite « firmware update in progress » comme
  cause de déconnexion temporaire).
- En octobre 2026, **aucun signalement** de mise à jour ayant désactivé ECHONET
  chez les utilisateurs de la méthode (publiée en août 2025 ; des utilisateurs
  en Europe la font tourner en parallèle de MELCloud).
- Si une future mise à jour la désactive, relancez simplement la section 4.
  Bloquer l'accès de l'adaptateur à Internet empêche les mises à jour, mais
  coupe aussi MELCloud Home.

## Outils d'aide (facultatif)

À la place des commandes curl et des extraits python, ce dépôt fournit un outil
en ligne de commande :

```bash
pipx install git+https://github.com/reihlinstinct/mitsubishi-echonet.git
mitsubishi-echonet status  --ip 192.168.1.50   # vérification (lecture seule)
mitsubishi-echonet enable  --ip 192.168.1.50   # activation
mitsubishi-echonet disable --ip 192.168.1.50   # désactivation
mitsubishi-echonet discover                    # trouver les appareils ECHONET du réseau
mitsubishi-echonet capabilities --ip 192.168.1.50   # capacités de l'adaptateur, minuteries comprises (lecture seule)
```

Ainsi qu'un script autonome pour allumer et éteindre directement le climatiseur
via /smart (sans ECHONET, sans dépendances, seulement python3 ; il fonctionne
aussi sans activation d'ECHONET) :

```bash
python3 scripts/ac_smart.py --ip 192.168.1.50 status   # état complet (lecture seule)
python3 scripts/ac_smart.py --ip 192.168.1.50 on       # allumage (garde le dernier mode et la dernière température)
python3 scripts/ac_smart.py --ip 192.168.1.50 off      # extinction

# set lit d'abord l'état actuel et ne change que ce qui est demandé :
python3 scripts/ac_smart.py set --ip 192.168.1.50 --mode cool --temp 24 --fan auto
python3 scripts/ac_smart.py set --ip 192.168.1.50 --vane-v swing --vane-h center
python3 scripts/ac_smart.py set --ip 192.168.1.50 --dehum 50 --power-saving on

# monitor mesure la consommation (W) et la consommation cumulée (kWh) et écrit un fichier CSV/JSON :
python3 scripts/ac_smart.py monitor --ip 192.168.1.50 --out energy.csv --interval 30
```

`set` couvre tout ce qui est pilotable dans le protocole : mode
(auto/heat/dry/cool/fan), température (16.0-31.0 par pas d'un demi-degré),
ventilateur (auto/s1-s4/full), les deux volets, niveau de déshumidification,
économie d'énergie, verrouillage de la télécommande et bip. Dans le conteneur,
les commandes sont identiques, sauf que le status de /smart s'appelle
smart-status (le status ordinaire du CLI reste basé sur ECHONET) :

```bash
docker run --rm --network host mitsubishi-echonet smart-status --ip 192.168.1.50
docker run --rm --network host mitsubishi-echonet on --ip 192.168.1.50
docker run --rm --network host mitsubishi-echonet set --ip 192.168.1.50 --mode cool --temp 24
docker run --rm --network host -v ./data:/data mitsubishi-echonet monitor --ip 192.168.1.50 --out /data/energy.csv
```

## Exécution dans un conteneur (sans rien installer sur l'ordinateur)

Si vous ne voulez pas installer python ni de paquets sur l'ordinateur, l'outil
tourne dans un conteneur (docker ou podman, exactement les mêmes commandes) :

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

- `--network host` est nécessaire sous Linux pour que la vérification UDP (et le
  balayage multicast de discover) atteigne l'adaptateur ; sans lui, status et
  discover ne recevront pas de réponse.
- L'image est minimale (Alpine, épinglée par digest), s'exécute avec un
  utilisateur non root et n'installe aucun paquet externe : seulement la
  bibliothèque standard de python.
- Les commandes curl et python des sections précédentes n'ont pas du tout besoin
  de conteneur : elles s'exécutent directement sur le système.

## Avertissements

- ECHONET Lite n'a **ni authentification ni chiffrement** : tout appareil du
  réseau peut piloter le climatiseur. À réserver à un réseau domestique de
  confiance (c'est aussi ce que dit la documentation officielle de Mitsubishi
  Nouvelle-Zélande).
- Température en degrés entiers uniquement (limite du protocole).
- L'adaptateur signale à MELCloud qu'ECHONET a été activé. MELCloud Home continue de fonctionner en parallèle.

## Sources

- Méthode d'activation et payload : https://github.com/scottyphillips/echonetlite_homeassistant/issues/226
- Recherche sur le protocole : https://github.com/ncaunt/meldec/issues/2
- Commandes enable/disable (melsmart) : https://github.com/mafredri/meltools
- Plugin Homebridge : https://github.com/karlvr/homebridge-echonet-hvac
- Intégration Home Assistant : https://github.com/scottyphillips/echonetlite_homeassistant
- Mitsubishi NZ (officiel) : https://www.mitsubishi-electric.co.nz/wifi/learn_echonet.aspx
- Guide MELCloud Home (officiel) : https://media.crystallize.com/mitsubishi-prod_cb88776/25/11/28/1/melcloud-home-user-manual-v1-1-1.pdf
- Automatisations Maison (officiel) : https://support.apple.com/en-us/102313
