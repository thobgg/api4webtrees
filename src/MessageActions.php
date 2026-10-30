<?php

declare(strict_types=1);

namespace Api4Webtrees;

use Fisharebest\Webtrees\Auth;
use Fisharebest\Webtrees\Contracts\UserInterface;
use Fisharebest\Webtrees\DB;
use Fisharebest\Webtrees\Registry;
use Fisharebest\Webtrees\Services\MessageService;
use Fisharebest\Webtrees\Services\UserService;
use Fisharebest\Webtrees\Tree;
use Fisharebest\Webtrees\Validator;
use Psr\Http\Message\ResponseInterface;
use Psr\Http\Message\ServerRequestInterface;

use function array_filter;
use function array_map;
use function array_unique;
use function array_values;
use function count;
use function date;
use function explode;
use function in_array;
use function is_array;
use function is_int;
use function is_string;
use function max;
use function mb_substr;
use function preg_match;
use function preg_match_all;
use function preg_replace;
use function rawurldecode;
use function response;
use function rtrim;
use function str_replace;
use function str_starts_with;
use function strtotime;
use function strtoupper;
use function trim;
use function usort;

/**
 * Stufe 42 - Nachrichten: webtrees' eigenes Nachrichtensystem (Block "Nachrichten" auf "Meine Seite", Tabelle message,
 * MessageService) als JSON, damit eine App einen Posteingang hat und Datensaetze an Mitforscher teilen kann - als
 * Nachricht mit dem Link auf die Seite. Nichts Eigenes: dieselbe Tabelle, derselbe Zustelldienst (intern, per
 * E-Mail oder beides, je nach Kontaktart des Empfaengers), dieselbe Empfaengerliste wie im Block.
 *
 * webtrees speichert nicht den reinen Text, sondern die fertige Mail-Fassung in der Sprache des Empfaengers
 * ("Hallo Anna ... hat dir folgende Nachricht gesendet. ---- Text ---- Diese Nachricht wurde beim Betrachten
 * folgender Adresse gesendet: URL"). Die Route zerlegt das: 'text' ist nur der Teil zwischen den Strichlinien,
 * 'url' die Adresse aus dem Nachspann, 'body' bleibt roh. 'links' nennt jeden Datensatz dieses Servers, der in Text
 * oder URL vorkommt - aufgeloest auf Art und Kennung, damit die App ihn oeffnet, ohne Adressen zu zerlegen.
 */
trait MessageActions
{
    private const int MESSAGES_PAGE = 50;

    /** Seiten von Datensaetzen in webtrees-Adressen: /tree/{baum}/<Pfad>/{xref} */
    private const array MESSAGE_RECORD_PATHS = [
        'individual'  => 'INDI',
        'family'      => 'FAM',
        'source'      => 'SOUR',
        'media'       => 'OBJE',
        'repository'  => 'REPO',
        'note'        => 'NOTE',
        'shared-note' => 'NOTE',
        'location'    => '_LOC',
        'submitter'   => 'SUBM',
    ];

    /**
     * GET Messages/{tree}?page=: der Posteingang des angemeldeten Benutzers, neueste zuerst, 50 je Seite; dazu
     * 'recipients', die Benutzer, denen er schreiben kann - mit der Filterung des webtrees-Blocks, zusaetzlich ohne
     * Konten ohne Kontaktart (webtrees stellt denen nichts zu, meldet aber Erfolg).
     */
    public function getMessagesAction(ServerRequestInterface $request): ResponseInterface
    {
        $tree = Validator::attributes($request)->tree();

        if (!Auth::check()) {
            return $this->error(403, 'not-logged-in');
        }

        $page     = max(1, Validator::queryParams($request)->integer('page', 1));
        $base_url = Validator::attributes($request)->string('base_url');
        $users    = Registry::container()->get(UserService::class);

        $rows = DB::table('message')
            ->where('user_id', '=', Auth::id())
            ->orderByDesc('message_id')
            ->skip(($page - 1) * self::MESSAGES_PAGE)
            ->take(self::MESSAGES_PAGE + 1)
            ->get();

        $messages = [];

        foreach ($rows->slice(0, self::MESSAGES_PAGE) as $row) {
            $messages[] = $this->messageJson($row, $tree, $base_url, $users);
        }

        return response([
            'ok'         => true,
            'messages'   => $messages,
            'recipients' => $this->messageRecipients($tree, $users),
            'page'       => $page,
            'nextPage'   => $rows->count() > self::MESSAGES_PAGE ? $page + 1 : null,
        ]);
    }

    /**
     * POST Messages/{tree}: Rumpf { to, subject?, body, url? } - 'to' ein Benutzername oder eine Liste. Gesendet wird
     * ueber webtrees' MessageService als der angemeldete Benutzer, wie im Browser (Kontaktart des Empfaengers
     * entscheidet ueber intern/E-Mail, 'url' steht in der E-Mail als Seite, von der aus gesendet wurde). Kein
     * Rundschreiben - das ist in webtrees Verwaltern vorbehalten. Antwort: ok, delivered (Benutzernamen).
     */
    public function postMessagesAction(ServerRequestInterface $request): ResponseInterface
    {
        $tree = Validator::attributes($request)->tree();

        if (!Auth::check()) {
            return $this->error(403, 'not-logged-in');
        }

        $body = $this->body($request);
        $to   = $body['to'] ?? '';
        $to   = is_array($to) ? array_values(array_filter($to, static fn ($v): bool => is_string($v) && trim($v) !== '')) : (is_string($to) && trim($to) !== '' ? [$to] : []);

        if ($to === []) {
            return $this->error(400, 'missing-to');
        }

        $text = trim(str_replace("\r", '', $this->str($body, 'body')));

        if ($text === '') {
            return $this->error(400, 'missing-body');
        }

        $subject = trim(preg_replace('/\s+/', ' ', $this->str($body, 'subject')) ?? '');
        $subject = mb_substr($subject, 0, 255);
        $url     = trim($this->str($body, 'url'));
        $ip      = Validator::attributes($request)->string('client-ip', '');
        $users   = Registry::container()->get(UserService::class);
        $service = Registry::container()->get(MessageService::class);
        $allowed = $this->messageRecipients($tree, $users);
        $names   = array_map(static fn (array $r): string => $r['user'], $allowed);

        $recipients = [];

        foreach (array_unique($to) as $name) {
            $name = trim($name);
            $user = $users->findByUserName($name);

            if ($user === null) {
                return $this->error(404, 'unknown-user');
            }

            if (!in_array($user->userName(), $names, true)) {
                return $this->error(403, 'contact-none');
            }

            $recipients[] = $user;
        }

        $delivered = [];

        foreach ($recipients as $user) {
            if (!$service->deliverMessage(Auth::user(), $user, $subject, $text, $url, $ip)) {
                return $this->error(500, 'send-failed');
            }

            $delivered[] = $user->userName();
        }

        return response(['ok' => true, 'delivered' => $delivered]);
    }

    /**
     * POST MessageDelete/{tree}: Rumpf { ids: [..] } - loescht nur eigene Nachrichten (wie der Block). Antwort: ok, deleted.
     */
    public function postMessageDeleteAction(ServerRequestInterface $request): ResponseInterface
    {
        if (!Auth::check()) {
            return $this->error(403, 'not-logged-in');
        }

        $ids = $this->body($request)['ids'] ?? [];
        $ids = is_array($ids) ? array_values(array_filter($ids, static fn ($v): bool => is_int($v) && $v > 0)) : [];

        if ($ids === []) {
            return $this->error(400, 'missing-ids');
        }

        $deleted = DB::table('message')
            ->where('user_id', '=', Auth::id())
            ->whereIn('message_id', $ids)
            ->delete();

        return response(['ok' => true, 'deleted' => $deleted]);
    }

    /** Fuer Info: wie viele Nachrichten im Posteingang des angemeldeten Benutzers liegen. */
    private function messageCount(): int
    {
        return Auth::check() ? DB::table('message')->where('user_id', '=', Auth::id())->count() : 0;
    }

    /**
     * @return array<string,mixed>
     */
    private function messageJson(object $row, Tree $tree, string $base_url, UserService $users): array
    {
        $body   = (string) $row->body;
        $sender = (string) $row->sender;
        $from   = $sender === '' ? null : $users->findByIdentifier($sender);
        [$text, $url] = $this->splitMessageBody($body);

        return [
            'id'         => (int) $row->message_id,
            'created'    => date('c', strtotime((string) $row->created) ?: 0),
            'sender'     => $sender,
            'senderName' => $from?->realName(),
            'senderUser' => $from?->userName(),
            'subject'    => (string) $row->subject,
            'text'       => $text,
            'url'        => $url,
            'body'       => $body,
            'links'      => $this->messageLinks($text . "\n" . $url, $tree, $base_url),
        ];
    }

    /**
     * Die gespeicherte Mail-Fassung zerlegen: der Text steht zwischen zwei Strichlinien, die Adresse im Nachspann.
     * Ohne Strichlinien (fremde Herkunft) ist der ganze Rumpf der Text.
     *
     * @return array{string,string}
     */
    private function splitMessageBody(string $body): array
    {
        $body  = str_replace("\r", '', $body);
        $parts = explode("\n------------------------------------------------------------\n", $body);

        if (count($parts) >= 3) {
            $text  = trim($parts[1]);
            $after = $parts[2];
        } else {
            $text  = trim($body);
            $after = '';
        }

        $url = '';

        if ($after !== '' && preg_match('~(https?://\S+)\s*$~', $after, $m) === 1) {
            $url = $m[1];
        }

        return [$text, $url];
    }

    /**
     * Adressen dieses Servers im Text, aufgeloest auf Datensatzart und Kennung - mit schoenen URLs
     * (/tree/baum/individual/I1/name) und ohne (index.php?route=%2Ftree%2Fbaum%2Findividual%2FI1). Datensaetze,
     * die der Leser nicht sehen darf, oder die es nicht gibt, bekommen xref null (die Art bleibt).
     *
     * @return list<array{url:string,type:string,xref:string|null}>
     */
    private function messageLinks(string $text, Tree $tree, string $base_url): array
    {
        $links = [];
        $seen  = [];
        $base  = rtrim($base_url, '/');

        preg_match_all('~https?://[^\s<>"\')\]]+~u', $text, $matches);

        foreach ($matches[0] as $url) {
            $url = rtrim($url, '.,;:');

            if ($base !== '' && !str_starts_with($url, $base . '/') && $url !== $base) {
                continue;
            }

            $path = $url;

            if (preg_match('~[?&]route=([^&#]+)~', $url, $m) === 1) {
                $path = rawurldecode($m[1]);
            }

            if (preg_match('~/tree/([^/?#]+)/([a-z-]+)/([^/?#]+)~', $path, $m) !== 1) {
                continue;
            }

            $type = self::MESSAGE_RECORD_PATHS[$m[2]] ?? null;

            if ($type === null || in_array($url, $seen, true)) {
                continue;
            }

            $seen[] = $url;
            $xref   = rawurldecode($m[3]);
            $record = rawurldecode($m[1]) === $tree->name() ? Registry::gedcomRecordFactory()->make($xref, $tree) : null;

            $links[] = [
                'url'  => $url,
                'type' => $type,
                'xref' => $record !== null && $record->canShow() ? $record->xref() : null,
            ];
        }

        return $links;
    }

    /**
     * Wem der angemeldete Benutzer schreiben kann - die Liste des webtrees-Blocks (alle freigeschalteten Konten ausser
     * dem eigenen, die den Baum sehen duerfen und Kontakt nicht abgeschaltet haben), zusaetzlich ohne Konten ohne
     * Kontaktart: webtrees' deliverMessage legt dann weder eine Nachricht an noch schickt es eine E-Mail.
     *
     * @return list<array{user:string,name:string,contact:string}>
     */
    private function messageRecipients(Tree $tree, UserService $users): array
    {
        $list = [];

        foreach ($users->all() as $user) {
            $contact = $user->getPreference(UserInterface::PREF_CONTACT_METHOD);

            if (
                $user->id() === Auth::id()
                || !$user->getPreference(UserInterface::PREF_IS_ACCOUNT_APPROVED)
                || ($tree->private() && Auth::accessLevel($tree, $user) > Auth::PRIV_USER)
                || $contact === '' || $contact === MessageService::CONTACT_METHOD_NONE
            ) {
                continue;
            }

            $list[] = ['user' => $user->userName(), 'name' => $user->realName(), 'contact' => $contact];
        }

        usort($list, static fn (array $a, array $b): int => strtoupper($a['name']) <=> strtoupper($b['name']) ?: $a['user'] <=> $b['user']);

        return $list;
    }
}
