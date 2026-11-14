<?php

declare(strict_types=1);

namespace Api4Webtrees;

use BackedEnum;
use Closure;
use Fisharebest\Webtrees\Auth;
use Fisharebest\Webtrees\Contracts\UserInterface;
use Fisharebest\Webtrees\Date\AbstractCalendarDate;
use Fisharebest\Webtrees\Fact;
use Fisharebest\Webtrees\I18N;
use Fisharebest\Webtrees\Individual;
use Fisharebest\Webtrees\MediaFile;
use Fisharebest\Webtrees\Registry;
use Illuminate\Support\Collection;

use function array_keys;
use function array_map;
use function class_exists;
use function enum_exists;
use function is_int;
use function is_string;
use function method_exists;

/**
 * Unterschiede zwischen webtrees 2.2 und 2.3 (main) an einer Stelle. Das Modul laeuft auf beiden Fassungen;
 * jede Methode sieht zur Laufzeit nach, was die Installation kennt.
 *
 * - Zugriffsstufen: 2.2 Auth::PRIV_* (int), ab 2.3 Enums\AccessLevel (Commit e787f3b024, 30.07.2026)
 * - Sortieren mit Sprachregeln: 2.2 I18N::comparator(), ab 2.3 I18N::compare() (Commit 35bbfee753, 05.08.2026)
 * - aktive Sprachen: 2.2 I18N::activeLocales(), ab 2.3 I18N::activeLanguages() (Commit 73cf3b3325, 22.07.2026)
 * - Fakten sortieren: 2.2 Fact::sortFacts(), ab 2.3 FactSortService::sort() (Commits 917bb0ab4b, 35bbfee753)
 * - Wasserzeichen: 2.2 ImageFactory::thumbnailNeedsWatermark(), ab 2.3 Auth::needsWatermark() (Commit 522967a0c7, 10.07.2026)
 * - Geschlecht: 2.2 Individual::sex() liefert string und RelationIsDescriptor::values() nimmt string, ab 2.3 Enums\Sex (Commit c3433c1850, 01.08.2026)
 * - Kalender eines Datums: 2.2 Klassenkonstante ESCAPE, ab 2.3 calendarEscape() mit Enums\CalendarEscape (Commit 73cf3b3325)
 */
final class Compat
{
    // Zugriffsstufen als Zahl - so wie Auth::PRIV_HIDE/PRIV_NONE/PRIV_USER/PRIV_PRIVATE in 2.2 und die Werte von
    // Enums\AccessLevel::Hidden/Manager/Member/Public in 2.3
    public const int HIDDEN  = -1;
    public const int MANAGER = 0;
    public const int MEMBER  = 1;
    public const int VISITOR = 2;

    private const string ACCESS_LEVEL      = 'Fisharebest\\Webtrees\\Enums\\AccessLevel';
    private const string FACT_SORT_SERVICE = 'Fisharebest\\Webtrees\\Services\\FactSortService';
    private const string SEX               = 'Fisharebest\\Webtrees\\Enums\\Sex';

    /**
     * Stufe "vor allen verbergen" fuer facts()/canShow() - liefert alle Fakten, auch private.
     */
    public static function hidden(): int|BackedEnum
    {
        return self::level(self::HIDDEN);
    }

    /**
     * Stufe "Mitglied" fuer canShow()/canShowName().
     */
    public static function member(): int|BackedEnum
    {
        return self::level(self::MEMBER);
    }

    /**
     * Stufe "Besucher" fuer canShow()/canShowName().
     */
    public static function visitor(): int|BackedEnum
    {
        return self::level(self::VISITOR);
    }

    /**
     * Zugriffsstufe als Zahl, zum Vergleichen - aus int (2.2) oder AccessLevel (2.3). Enums lassen sich in PHP
     * nicht mit < oder > vergleichen (immer false), deshalb hier ueber den Wert.
     */
    public static function levelValue(int|BackedEnum $level): int
    {
        return is_int($level) ? $level : (int) $level->value;
    }

    /**
     * Zwei Zeichenketten nach den Regeln der aktuellen Sprache vergleichen (fuer usort/sort).
     *
     * @return Closure(string,string):int
     */
    public static function comparator(): Closure
    {
        if (method_exists(I18N::class, 'comparator')) {
            return I18N::comparator();
        }

        return static fn (string $x, string $y): int => I18N::compare($x, $y);
    }

    /**
     * Sprachkennungen ("de", "en-US", ...) der in dieser Installation aktiven Sprachen.
     *
     * @return list<string>
     */
    public static function activeLanguageTags(): array
    {
        if (method_exists(I18N::class, 'activeLanguages')) {
            return array_keys(I18N::activeLanguages());
        }

        return array_map(static fn ($locale): string => $locale->languageTag(), I18N::activeLocales());
    }

    /**
     * Fakten in der Reihenfolge, die webtrees auf der Personenseite zeigt.
     *
     * @param Collection<int,Fact> $facts
     *
     * @return Collection<int,Fact>
     */
    public static function sortFacts(Collection $facts): Collection
    {
        if (class_exists(self::FACT_SORT_SERVICE)) {
            return Registry::container()->get(self::FACT_SORT_SERVICE)->sort($facts);
        }

        return Fact::sortFacts($facts);
    }

    /**
     * Geschlecht als Buchstabe (M, F, X, U) - 2.2 liefert sex() den Buchstaben, 2.3 ein Enum mit ihm als Wert.
     */
    public static function sex(Individual $individual): string
    {
        $sex = $individual->sex();

        return is_string($sex) ? $sex : (string) $sex->value;
    }

    /**
     * Geschlecht als Argument fuer webtrees-Methoden, die 2.2 den Buchstaben und 2.3 das Enum erwarten.
     */
    public static function sexArgument(string $sex): string|BackedEnum
    {
        if (enum_exists(self::SEX)) {
            return (self::SEX)::tryFrom($sex) ?? (self::SEX)::from('U');
        }

        return $sex;
    }

    /**
     * Kalenderkennung eines Datums, wie sie im GEDCOM steht ("@#DGREGORIAN@", "@#DJULIAN@", ...).
     */
    public static function calendarEscape(AbstractCalendarDate $date): string
    {
        if (method_exists($date, 'calendarEscape')) {
            return (string) $date->calendarEscape()->value;
        }

        return $date::ESCAPE;
    }

    /**
     * Bekommt dieser Benutzer die Bilder der Mediendatei nur mit Wasserzeichen? Dann liefert das Modul keinen
     * Portraitausschnitt, sondern verweist auf das Vorschaubild von webtrees.
     */
    public static function needsWatermark(MediaFile $media_file, UserInterface $user): bool
    {
        if (method_exists(Auth::class, 'needsWatermark')) {
            return Auth::needsWatermark($media_file->media()->tree(), $user);
        }

        return Registry::imageFactory()->thumbnailNeedsWatermark($media_file, $user);
    }

    private static function level(int $value): int|BackedEnum
    {
        if (enum_exists(self::ACCESS_LEVEL)) {
            return (self::ACCESS_LEVEL)::from($value);
        }

        return $value;
    }
}
