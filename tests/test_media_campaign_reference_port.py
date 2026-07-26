from __future__ import annotations

from aicrm_next.media_library import campaign_reference_port


def test_media_campaign_reference_port_is_supplied_by_composition_root(monkeypatch) -> None:
    class Port:
        def list_image_references_dbapi(self, executor, *, image_library_id):
            return [{"id": int(image_library_id)}]

        def clear_image_references_dbapi(self, executor, *, image_library_id):
            return int(image_library_id)

    monkeypatch.setattr(campaign_reference_port, "_FACTORY", campaign_reference_port._FACTORY)
    campaign_reference_port.configure_campaign_media_reference_port(Port)

    port = campaign_reference_port.build_campaign_media_reference_port()
    assert port.list_image_references_dbapi(object(), image_library_id=7) == [{"id": 7}]
    assert port.clear_image_references_dbapi(object(), image_library_id=7) == 7
