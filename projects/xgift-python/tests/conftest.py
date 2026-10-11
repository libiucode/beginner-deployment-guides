"""All HTTP integration tests must supply explicit fixture transports."""
import pytest
import httpx

@pytest.fixture(autouse=True)
def forbid_unmocked_httpx(monkeypatch):
    send=httpx.Client.send
    def fixture_only(self,request,*args,**kwargs):
        if not isinstance(self._transport,httpx.MockTransport):
            raise AssertionError('Unmocked HTTP request forbidden during tests')
        return send(self,request,*args,**kwargs)
    monkeypatch.setattr(httpx.Client,'send',fixture_only)
