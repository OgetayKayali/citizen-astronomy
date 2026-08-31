from __future__ import annotations

import unittest

from photometry_app.core.aavso_aid import (
    AidFilterRejectedAllError,
    AidObservation,
    AidQuery,
    aid_observations_to_import,
    analyze_aid_filters,
    download_aid_photometry,
    filter_aid_observations,
    format_aid_download_notes,
    parse_official_aid_page,
    parse_vsx_aid_document,
)


_VSX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<VSXObject><Name>DY Her</Name><AUID>000-BBX-619</AUID><Data>
<Observation><Id>1</Id><JD>2460018.78658</JD><Mag>10.461</Mag><Uncertainty>0.005</Uncertainty>
<ObsType>CCD</ObsType><Band>V</Band><ObsCode>SAH</ObsCode><ValFlag>Z</ValFlag><Name>DY HER</Name>
<MType>STD</MType></Observation>
<Observation><Id>2</Id><JD>2460018.78771</JD><Mag>10.442</Mag><Uncertainty>0.005</Uncertainty>
<ObsType>CCD</ObsType><Band>V</Band><ObsCode>SAH</ObsCode><ValFlag>Z</ValFlag><Name>DY HER</Name>
<MType>STD</MType></Observation>
<Observation><Id>3</Id><JD>2460100.10000</JD><Mag>&lt;12.5</Mag><Uncertainty></Uncertainty>
<ObsType>VIS</ObsType><Band>Vis.</Band><ObsCode>KAY</ObsCode><ValFlag>T</ValFlag><Name>DY HER</Name>
<FainterThan>1</FainterThan></Observation>
<Count>3</Count></Data></VSXObject>
"""

_VSX_CSV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<VSXObject><Name>DY Her</Name><Data><![CDATA[JD,mag,uncert,band,by,val,starName,mtype,obsID,fainterThan,obsType
2460018.78658,10.461,"0.005",V,SAH,Z,DY HER,STD,1,0,CCD
2460018.78771,10.442,"0.005",V,SAH,Z,DY HER,STD,2,0,CCD
]]><Count>2</Count></Data></VSXObject>
"""


class _FakeResponse:
    def __init__(self, *, payload=None, text="", status_code=200) -> None:
        self._payload = payload
        self.text = text
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class AavsoAidTest(unittest.TestCase):
    def test_parse_official_page_and_filters(self) -> None:
        observations, next_url, available = parse_official_aid_page(
            {
                "count": 2,
                "next": "https://apps.aavso.org/v2/api/observations/photometry/?page=2",
                "results": [
                    {
                        "id": 11,
                        "name": "DY HER",
                        "obscode": "SAH",
                        "jd_dbl": 2460018.78658,
                        "magnitude": "10.461",
                        "uncertainty": "0.005",
                        "band": "V",
                        "obstype": "C",
                        "auid": "000-BBX-619",
                        "fainterthan": False,
                    },
                    {
                        "id": 12,
                        "name": "DY HER",
                        "obscode": "KAY",
                        "jd_dbl": 2460018.9,
                        "magnitude": "<12.1",
                        "uncertainty": "",
                        "band": "Vis",
                        "obstype": "V",
                        "auid": "000-BBX-619",
                        "fainterthan": True,
                    },
                ],
            }
        )
        self.assertEqual(available, 2)
        self.assertTrue(next_url.endswith("page=2"))
        self.assertEqual(len(observations), 2)
        kept = filter_aid_observations(
            observations,
            AidQuery(star_name="DY Her", band="2", obstype="CCD", exclude_fainterthan=True),
        )
        self.assertEqual(len(kept), 1)
        self.assertAlmostEqual(kept[0].magnitude, 10.461)

    def test_parse_vsx_xml_and_csv(self) -> None:
        xml_rows, xml_count, xml_name = parse_vsx_aid_document(_VSX_XML)
        self.assertEqual(xml_name, "DY Her")
        self.assertEqual(xml_count, 3)
        self.assertEqual(len(xml_rows), 3)
        self.assertTrue(xml_rows[2].fainterthan)
        csv_rows, csv_count, csv_name = parse_vsx_aid_document(_VSX_CSV_XML)
        self.assertEqual(csv_name, "DY Her")
        self.assertEqual(csv_count, 2)
        self.assertEqual(len(csv_rows), 2)
        self.assertEqual(csv_rows[0].obstype, "CCD")

    def test_group_by_night_splits_on_gap_and_band(self) -> None:
        observations = [
            AidObservation(2460018.78, 10.46, 0.005, band="V", obstype="CCD"),
            AidObservation(2460018.79, 10.44, 0.005, band="V", obstype="CCD"),
            AidObservation(2460020.78, 10.50, 0.005, band="V", obstype="CCD"),
            AidObservation(2460020.79, 10.52, 0.005, band="B", obstype="CCD"),
        ]
        imported = aid_observations_to_import(observations, star_name="DY Her", source_id="vsx-1")
        sizes = sorted(len(session.series.points) for session in imported.sessions)
        self.assertEqual(len(imported.sessions), 3)
        self.assertEqual(sizes, [1, 1, 2])
        self.assertEqual(imported.sessions[0].series.source_name, "DY Her")
        self.assertTrue(all(session.session_name.startswith("AAVSO ") for session in imported.sessions))

    def test_download_uses_vsx_without_token(self) -> None:
        def fake_get(url, **kwargs):
            self.assertIn("vsx.aavso.org", url)
            self.assertEqual(kwargs["params"]["ident"], "DY Her")
            self.assertEqual(kwargs["params"]["band"], "V")
            return _FakeResponse(text=_VSX_XML)

        result = download_aid_photometry(
            AidQuery(star_name="DY Her", band="2", obstype="CCD", start_jd=2460018.0, end_jd=2460019.0),
            request_get=fake_get,
        )
        self.assertEqual(result.source, "vsx")
        self.assertEqual(result.kept_count, 2)
        self.assertEqual(len(result.imported.sessions), 1)

    def test_download_uses_official_api_when_token_present(self) -> None:
        calls: list[str] = []

        def fake_get(url, **kwargs):
            calls.append(url)
            headers = kwargs.get("headers") or {}
            self.assertEqual(headers.get("Authorization"), "Token secret-token")
            if "page=2" in url:
                return _FakeResponse(
                    payload={"count": 2, "next": None, "results": []},
                )
            return _FakeResponse(
                payload={
                    "count": 2,
                    "next": "https://apps.aavso.org/v2/api/observations/photometry/?page=2",
                    "results": [
                        {
                            "id": 11,
                            "name": "DY HER",
                            "obscode": "SAH",
                            "jd_dbl": 2460018.78658,
                            "magnitude": "10.461",
                            "uncertainty": "0.005",
                            "band": "V",
                            "obstype": "C",
                            "auid": "000-BBX-619",
                            "fainterthan": False,
                        }
                    ],
                }
            )

        result = download_aid_photometry(
            AidQuery(
                star_name="DY Her",
                api_token="secret-token",
                band="2",
                obstype="CCD",
                start_jd=2460018.0,
                end_jd=2460019.0,
            ),
            request_get=fake_get,
            sleep=lambda _seconds: None,
        )
        self.assertEqual(result.source, "aavso-api")
        self.assertEqual(result.kept_count, 1)
        self.assertEqual(len(calls), 2)

    def test_download_requires_star_name(self) -> None:
        with self.assertRaises(ValueError):
            download_aid_photometry(AidQuery(star_name="  "))

    def test_filter_report_names_observer_as_the_killer(self) -> None:
        observations = [
            AidObservation(2460126.1, 11.2, 0.01, band="V", obstype="CCD", observer="SAH", mtype="STD"),
            AidObservation(2460126.2, 11.3, 0.01, band="V", obstype="CCD", observer="KAY", mtype="STD"),
            AidObservation(2460500.1, 11.4, 0.01, band="B", obstype="VIS", observer="SAH", mtype="STD"),
        ]
        query = AidQuery(
            star_name="AE UMa",
            start_jd=2460126.0,
            end_jd=2461126.0,
            band="",
            obstype="",
            mtype="STD",
            observer="OKDA",
            exclude_fainterthan=False,
            skip_discrepant=False,
            group_by_night=False,
        )
        analysis = analyze_aid_filters(observations, query)
        self.assertEqual(analysis.kept, [])
        observer_step = next(step for step in analysis.steps if step.name == "Observer")
        self.assertEqual(observer_step.removed, 3)
        self.assertEqual(observer_step.remaining, 0)
        notes = format_aid_download_notes(
            query,
            source="vsx",
            fetched_count=3,
            available_count=3,
            truncated=False,
            analysis=analysis,
            imported_sessions=0,
        )
        self.assertTrue(any("Observer (OKDA): 3 in, removed 3" in line for line in notes))
        self.assertTrue(any("AID returned observers: SAH (2), KAY (1)." in line for line in notes))

        def fake_get(url, **kwargs):
            return _FakeResponse(text=_VSX_XML)

        with self.assertRaises(AidFilterRejectedAllError) as raised:
            download_aid_photometry(
                AidQuery(star_name="DY Her", observer="OKDA", exclude_fainterthan=False, skip_discrepant=False),
                request_get=fake_get,
            )
        self.assertIn("Observer", str(raised.exception))
        self.assertTrue(any("AID filter Observer" in line for line in raised.exception.notes))
