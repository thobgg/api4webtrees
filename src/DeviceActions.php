<?php

declare(strict_types=1);

namespace Api4Webtrees;

use Fisharebest\Webtrees\Auth;
use Fisharebest\Webtrees\Contracts\UserInterface;
use Fisharebest\Webtrees\DB;
use Fisharebest\Webtrees\Log;
use Fisharebest\Webtrees\Registry;
use Fisharebest\Webtrees\Services\UserService;
use Fisharebest\Webtrees\Session;
use Fisharebest\Webtrees\Validator;
use Psr\Http\Message\ResponseInterface;
use Psr\Http\Message\ServerRequestInterface;

use function array_slice;
use function bin2hex;
use function explode;
use function hash;
use function hash_equals;
use function implode;
use function mb_substr;
use function preg_match;
use function preg_replace;
use function random_bytes;
use function redirect;
use function response;
use function strlen;
use function substr;
use function time;
use function trim;
use function usort;

/**
 * Stufe 47 - Geraete-Token: Eine App bleibt angemeldet, auch wenn der Server die webtrees-Sitzung aufraeumt.
 *
 * Anlass: webtrees legt keine Sitzungsdauer fest; PHP loescht Sitzungen nach session.gc_maxlifetime (Standard 24
 * Minuten), wo das Aufraeumen laeuft (z. B. Synology Web Station) - die App fragte dort bei jedem Start nach dem
 * Passwort. Ein Modul kann die Sitzungsdauer nicht selbst verlaengern (die Sitzung startet vor den Modulen).
 *
 * Verfahren wie bei Auffrischungs-Token ueblich (RFC 9700, Abschnitt 2.2.2: Wechsel bei jeder Benutzung, ein
 * wiederverwendetes altes Token sperrt das Geraet):
 *   - Das Token heisst <kennung>.<geheimnis>. Gespeichert wird je Geraet eine Benutzereinstellung mit dem SHA-256 des
 *     Geheimnisses - nie das Geheimnis selbst. Es ist zufaellig (256 Bit), deshalb genuegt ein schneller Hash.
 *   - Jedes Einloesen liefert ein neues Token. Der Vorgaenger gilt noch DEVICE_GRACE_SECONDS (die Antwort kann
 *     verloren gehen, zwei Teile der App koennen gleichzeitig einloesen).
 *   - Ein aelteres Token, oder eines mit bekannter Kennung und falschem Geheimnis: Geraet getrennt, Eintrag im
 *     Anmeldeprotokoll.
 *   - DEVICE_IDLE_SECONDS ohne Benutzung: verfallen. Passwort geaendert: ungueltig (Fingerabdruck des Passwort-Hashs).
 *   - Das Token meldet nur an (Auth::login) - nichts, was ein weiteres Modul der Sitzung danach abverlangt
 *     (zweite Stufe), wird uebersprungen.
 */
trait DeviceActions
{
    // Name der Benutzereinstellung: Vorsatz + Kennung (16 Hex-Zeichen) = 27 Zeichen; die Spalte fasst 32.
    private const string DEVICE_PREFIX        = 'api4wt_dev_';
    private const int    DEVICE_ID_BYTES      = 8;
    private const int    DEVICE_SECRET_BYTES  = 32;
    private const int    DEVICE_GRACE_SECONDS = 120;
    private const int    DEVICE_IDLE_SECONDS  = 90 * 86400;
    private const int    DEVICE_NAME_MAX      = 32;
    // So viele Geraete je Benutzer; das am laengsten unbenutzte faellt heraus.
    private const int    DEVICE_MAX           = 20;
    // Gleichzeitiges Einloesen: so oft neu lesen, wenn ein anderer Aufruf den Eintrag gerade geaendert hat.
    private const int    DEVICE_RETRIES       = 3;

    /**
     * Rumpf { deviceToken }: einloesen - die Sitzung ist danach als der Benutzer des Tokens angemeldet, die Antwort
     * traegt das naechste Token. Geht mit und ohne Anmeldung (angemeldet: nur das Token wechselt).
     * Rumpf { deviceName } (angemeldet): ein Token fuer dieses Geraet ausstellen.
     */
    public function postDeviceAction(ServerRequestInterface $request): ResponseInterface
    {
        $body  = $this->body($request);
        $token = $this->str($body, 'deviceToken');

        if ($token !== '') {
            return $this->deviceRedeem($token);
        }

        if (!Auth::check()) {
            return $this->error(403, 'not-logged-in');
        }

        $user = Auth::user();

        return response([
            'ok'          => true,
            'deviceToken' => $this->deviceIssue($user, $this->str($body, 'deviceName')),
            'user'        => $user->userName(),
        ]);
    }

    /**
     * Rumpf { deviceToken }: dieses Geraet trennen (Abmelden in der App). Braucht keine Anmeldung - wer das Token hat,
     * darf es wegwerfen. Die Antwort ist immer { ok: true }; sie verraet nicht, ob es das Token gab.
     */
    public function postDeviceDeleteAction(ServerRequestInterface $request): ResponseInterface
    {
        $parts = self::deviceParse($this->str($this->body($request), 'deviceToken'));

        if ($parts !== null) {
            $row = self::deviceRow($parts[0]);

            if ($row !== null) {
                $device = self::deviceDecode((string) $row->setting_value);
                $hash   = hash('sha256', $parts[1]);

                if (hash_equals($device['hash'], $hash) || ($device['prev'] !== '' && hash_equals($device['prev'], $hash))) {
                    self::deviceForget((int) $row->user_id, $parts[0]);
                }
            }
        }

        return response(['ok' => true]);
    }

    /**
     * "Trennen" in der Geraeteliste der Seite "App": Formular { id, url }, nur eigene Geraete.
     */
    public function postDeviceOffAction(ServerRequestInterface $request): ResponseInterface
    {
        $id = Validator::parsedBody($request)->string('id', '');

        if (Auth::check() && preg_match('/^[0-9a-f]{' . (2 * self::DEVICE_ID_BYTES) . '}$/', $id) === 1) {
            self::deviceForget((int) Auth::id(), $id);
        }

        return redirect(Validator::parsedBody($request)->isLocalUrl()->string('url', $this->actionUrl('App', null)));
    }

    /**
     * Neues Geraet fuer diesen Benutzer anlegen; liefert das Token (nur hier und beim Einloesen ist es im Klartext da).
     */
    private function deviceIssue(UserInterface $user, string $name): string
    {
        // Platz schaffen: Verfallenes weg, und hoechstens DEVICE_MAX - 1 bleiben neben dem neuen.
        $devices = $this->devices($user);

        foreach (array_slice($devices, self::DEVICE_MAX - 1) as $old) {
            self::deviceForget($user->id(), $old['id']);
        }

        $id     = bin2hex(random_bytes(self::DEVICE_ID_BYTES));
        $secret = bin2hex(random_bytes(self::DEVICE_SECRET_BYTES));
        $now    = time();

        DB::table('user_setting')->insert([
            'user_id'       => $user->id(),
            'setting_name'  => self::DEVICE_PREFIX . $id,
            'setting_value' => self::deviceEncode([
                'hash'      => hash('sha256', $secret),
                'prev'      => '',
                'prevUntil' => 0,
                'created'   => $now,
                'used'      => $now,
                'password'  => self::passwordFingerprint($user->id()),
                'name'      => self::deviceName($name),
            ]),
        ]);

        return $id . '.' . $secret;
    }

    private function deviceRedeem(string $token): ResponseInterface
    {
        $parts = self::deviceParse($token);

        if ($parts === null) {
            return $this->error(400, 'device-invalid');
        }

        [$id, $secret] = $parts;
        $hash          = hash('sha256', $secret);

        for ($try = 0; $try < self::DEVICE_RETRIES; $try++) {
            $row = self::deviceRow($id);

            if ($row === null) {
                return $this->error(403, 'device-invalid');
            }

            $user_id = (int) $row->user_id;
            $user    = Registry::container()->get(UserService::class)->find($user_id);
            $device  = self::deviceDecode((string) $row->setting_value);
            $now     = time();

            if ($user === null) {
                self::deviceForget($user_id, $id);

                return $this->error(403, 'device-invalid');
            }

            $current  = hash_equals($device['hash'], $hash);
            $previous = !$current && $device['prev'] !== '' && hash_equals($device['prev'], $hash);

            // Weder das aktuelle Token noch der Vorgaenger in seiner Frist: Jemand benutzt ein altes Token - das Geraet
            // selbst oder wer es abgegriffen hat. Welcher von beiden, laesst sich nicht sagen: Geraet trennen.
            if (!$current && !($previous && $now <= $device['prevUntil'])) {
                self::deviceForget($user_id, $id);
                Log::addAuthenticationLog('Geräte-Token abgewiesen und Gerät getrennt (altes oder falsches Token): ' . $user->userName() . '/' . $device['name']);

                return $this->error(403, 'device-revoked');
            }

            if ($now - $device['used'] > self::DEVICE_IDLE_SECONDS || !hash_equals($device['password'], self::passwordFingerprint($user_id))) {
                self::deviceForget($user_id, $id);

                return $this->error(403, 'device-expired');
            }

            // Wie beim Einmal-Code (Pair) und bei der Anmeldung von webtrees: nur bestaetigte und freigegebene Konten.
            if ($user->getPreference(UserInterface::PREF_IS_EMAIL_VERIFIED) !== '1' || $user->getPreference(UserInterface::PREF_IS_ACCOUNT_APPROVED) !== '1') {
                return $this->error(403, 'device-invalid');
            }

            $next    = bin2hex(random_bytes(self::DEVICE_SECRET_BYTES));
            $changed = DB::table('user_setting')
                ->where('user_id', '=', $user_id)
                ->where('setting_name', '=', self::DEVICE_PREFIX . $id)
                ->where('setting_value', '=', (string) $row->setting_value)
                ->update(['setting_value' => self::deviceEncode([
                    'hash'      => hash('sha256', $next),
                    // Der Vorgaenger ist immer das bisher aktuelle Token - auch wenn gerade dessen Vorgaenger eingeloest
                    // wurde: dann haelt ein anderer Teil der App das bisher aktuelle und darf es noch die Frist lang benutzen.
                    'prev'      => $device['hash'],
                    'prevUntil' => $now + self::DEVICE_GRACE_SECONDS,
                    'used'      => $now,
                ] + $device)]);

            // Ein gleichzeitiger Aufruf war schneller: neu lesen - das eigene Token ist jetzt der Vorgaenger in seiner Frist.
            if ($changed !== 1) {
                continue;
            }

            if (Auth::id() !== $user_id) {
                // Als jemand anderes angemeldet: dessen Sitzung nicht weiterreichen (webtrees' Anmeldung behaelt die Daten
                // der Sitzung). 'initiated' wie Session::start(), sonst verwirft der naechste Aufruf die neue Sitzung.
                if (Auth::check()) {
                    Auth::logout();
                    Session::put('initiated', true);
                }

                Auth::login($user);
                Log::addAuthenticationLog('Login (App, Geräte-Token): ' . $user->userName() . '/' . $user->realName());
                $user->setPreference(UserInterface::PREF_TIMESTAMP_ACTIVE, (string) $now);
            }

            return response([
                'ok'          => true,
                'deviceToken' => $id . '.' . $next,
                'user'        => $user->userName(),
                // Wechselt, wenn vorher jemand anderes angemeldet war - sonst dasselbe wie in Info.
                'csrf'        => Session::getCsrfToken(),
            ]);
        }

        // Nur bei anhaltend gleichzeitigen Aufrufen; das Token gilt weiter, der Client versucht es spaeter noch einmal.
        return $this->error(409, 'device-busy');
    }

    /**
     * Die Geraete eines Benutzers fuer die Seite "App", zuletzt benutzte zuerst. Verfallene werden dabei geloescht.
     *
     * @return list<array{id:string,name:string,created:int,used:int}>
     */
    private function devices(UserInterface $user): array
    {
        $rows = DB::table('user_setting')
            ->where('user_id', '=', $user->id())
            ->where('setting_name', 'LIKE', self::DEVICE_PREFIX . '%')
            ->get();

        $devices     = [];
        $fingerprint = self::passwordFingerprint($user->id());
        $now         = time();

        foreach ($rows as $row) {
            $id     = substr((string) $row->setting_name, strlen(self::DEVICE_PREFIX));
            $device = self::deviceDecode((string) $row->setting_value);

            if ($now - $device['used'] > self::DEVICE_IDLE_SECONDS || !hash_equals($device['password'], $fingerprint)) {
                self::deviceForget($user->id(), $id);
                continue;
            }

            $devices[] = ['id' => $id, 'name' => $device['name'], 'created' => $device['created'], 'used' => $device['used']];
        }

        usort($devices, static fn (array $a, array $b): int => $b['used'] <=> $a['used']);

        return $devices;
    }

    /**
     * @return array{0:string,1:string}|null Kennung und Geheimnis
     */
    private static function deviceParse(string $token): array|null
    {
        if (preg_match('/^([0-9a-f]{' . (2 * self::DEVICE_ID_BYTES) . '})\.([0-9a-f]{' . (2 * self::DEVICE_SECRET_BYTES) . '})$/', $token, $match) !== 1) {
            return null;
        }

        return [$match[1], $match[2]];
    }

    private static function deviceRow(string $id): object|null
    {
        return DB::table('user_setting')->where('setting_name', '=', self::DEVICE_PREFIX . $id)->first();
    }

    private static function deviceForget(int $user_id, string $id): void
    {
        DB::table('user_setting')->where('user_id', '=', $user_id)->where('setting_name', '=', self::DEVICE_PREFIX . $id)->delete();
    }

    /**
     * Der Wert der Benutzereinstellung: hash|prev|prevUntil|created|used|password|name - hoechstens
     * 64 + 64 + 10 + 10 + 10 + 16 + 32 + 6 = 212 Zeichen; die Spalte fasst 255.
     *
     * @param array{hash:string,prev:string,prevUntil:int,created:int,used:int,password:string,name:string} $device
     */
    private static function deviceEncode(array $device): string
    {
        return implode('|', [$device['hash'], $device['prev'], $device['prevUntil'], $device['created'], $device['used'], $device['password'], $device['name']]);
    }

    /**
     * @return array{hash:string,prev:string,prevUntil:int,created:int,used:int,password:string,name:string}
     */
    private static function deviceDecode(string $value): array
    {
        $parts = explode('|', $value, 7) + ['', '', '0', '0', '0', '', ''];

        return [
            'hash'      => $parts[0],
            'prev'      => $parts[1],
            'prevUntil' => (int) $parts[2],
            'created'   => (int) $parts[3],
            'used'      => (int) $parts[4],
            'password'  => $parts[5],
            'name'      => $parts[6],
        ];
    }

    /** Der Name, den die App dem Geraet gibt ("Pixel 8", "wtWin auf BUERO-PC") - nur zur Anzeige in der Geraeteliste. */
    private static function deviceName(string $name): string
    {
        $name = trim((string) preg_replace('/[\p{C}|]+/u', ' ', $name));
        $name = mb_substr($name, 0, self::DEVICE_NAME_MAX);

        return $name === '' ? 'App' : $name;
    }

    /**
     * Aendert sich das Passwort, aendert sich sein Hash - und damit dieser Fingerabdruck: alle Geraete-Token des
     * Benutzers sind dann ungueltig. (Auch wenn webtrees den Hash nach einem PHP-Wechsel neu berechnet - dann fragt
     * die App einmal nach dem Passwort.) Aus dem Fingerabdruck laesst sich der Passwort-Hash nicht zurueckgewinnen.
     */
    private static function passwordFingerprint(int $user_id): string
    {
        return substr(hash('sha256', (string) DB::table('user')->where('user_id', '=', $user_id)->value('password')), 0, 16);
    }
}
