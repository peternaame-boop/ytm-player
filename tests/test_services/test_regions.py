"""Tests for the YouTube Music chart-region list."""

from __future__ import annotations

import pytest


class TestChartRegions:
    def test_module_imports(self):
        from ytm_player.services import regions

        assert hasattr(regions, "CHART_REGIONS")

    def test_regions_is_tuple_of_pairs(self):
        from ytm_player.services.regions import CHART_REGIONS

        assert isinstance(CHART_REGIONS, tuple)
        assert all(isinstance(item, tuple) and len(item) == 2 for item in CHART_REGIONS)

    def test_codes_are_uppercase_2char(self):
        from ytm_player.services.regions import CHART_REGIONS

        for code, name in CHART_REGIONS:
            assert isinstance(code, str)
            assert isinstance(name, str)
            assert code.isupper(), f"non-uppercase code: {code!r}"
            assert len(code) == 2, f"non-2-char code: {code!r}"

    def test_names_non_empty(self):
        from ytm_player.services.regions import CHART_REGIONS

        for code, name in CHART_REGIONS:
            assert name.strip(), f"empty name for code {code!r}"

    def test_no_duplicate_codes(self):
        from ytm_player.services.regions import CHART_REGIONS

        codes = [c for c, _ in CHART_REGIONS]
        assert len(codes) == len(set(codes)), "duplicate codes present"

    def test_us_present(self):
        """US is the default region in settings; must always be available."""
        from ytm_player.services.regions import CHART_REGIONS

        codes = [c for c, _ in CHART_REGIONS]
        assert "US" in codes

    def test_global_first_then_alphabetical(self):
        """Global ("ZZ") sits at position 0 as the default; rest alphabetical."""
        from ytm_player.services.regions import CHART_REGIONS

        assert CHART_REGIONS[0] == ("ZZ", "Global")
        rest = [name for _, name in CHART_REGIONS[1:]]
        assert rest == sorted(rest), "non-global regions not alphabetical by name"

    def test_minimum_size(self):
        """YouTube's advertised list is 62 codes (including ZZ = Global);
        we add 6 historically-supported ones outside that list. Total ≥ 60."""
        from ytm_player.services.regions import CHART_REGIONS

        assert len(CHART_REGIONS) >= 60

    def test_spain_present(self):
        """Spain was reported missing in issue #73 (user tried locale-style ES-ES)."""
        from ytm_player.services.regions import CHART_REGIONS

        codes = [c for c, _ in CHART_REGIONS]
        assert "ES" in codes


class TestNormaliseRegion:
    def test_two_letter_passthrough(self):
        from ytm_player.services.regions import normalise_region

        assert normalise_region("ES") == "ES"
        assert normalise_region("US") == "US"

    def test_lowercase_uppercased(self):
        from ytm_player.services.regions import normalise_region

        assert normalise_region("es") == "ES"
        assert normalise_region("gb") == "GB"

    def test_locale_dash_stripped(self):
        """Locale territory, not language, determines the chart region."""
        from ytm_player.services.regions import normalise_region

        assert normalise_region("ES-ES") == "ES"
        assert normalise_region("ES-MX") == "MX"
        assert normalise_region("en-GB") == "GB"

    def test_locale_underscore_stripped(self):
        """es_ES (POSIX locale) → ES."""
        from ytm_player.services.regions import normalise_region

        assert normalise_region("es_ES") == "ES"
        assert normalise_region("en_GB") == "GB"

    @pytest.mark.parametrize("value", ["en-GB", "en_GB", " EN_gb "])
    async def test_charts_receives_territory_not_language(self, ytmusic_service, value):
        ytmusic_service._ytm.get_charts.return_value = {"songs": []}

        assert await ytmusic_service.get_charts(value) == {"songs": []}
        ytmusic_service._ytm.get_charts.assert_called_once_with(country="GB")

    def test_existing_bare_values_are_preserved(self):
        from ytm_player.services.regions import CHART_REGIONS, normalise_region

        for code, name in CHART_REGIONS:
            assert normalise_region(code.lower()) == code
            assert normalise_region(name) == name.upper()
        assert normalise_region("auto") == "AUTO"
        assert normalise_region("  ") == ""

    def test_whitespace_trimmed(self):
        from ytm_player.services.regions import normalise_region

        assert normalise_region("  es-ES  ") == "ES"

    def test_empty_returns_empty(self):
        from ytm_player.services.regions import normalise_region

        assert normalise_region("") == ""
