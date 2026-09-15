from pathlib import Path
p=Path('/mnt/data/v44fix41/scraper.py')
s=p.read_text(encoding='utf-8')
# replace _finance_row_values function
start=s.index('def _finance_row_values(')
end=s.index('\ndef _find_finance_row', start)
new=r'''def _finance_row_values(row: dict, actual_keys: list) -> dict:
    """재무 행의 값 구조가 dict/list 어느 쪽이어도 최대한 보수적으로 읽는다."""
    sources = []
    for key in ("columns", "values", "data", "cells", "valueList", "items"):
        value = row.get(key)
        if value is not None:
            sources.append(value)
    out = {}
    keyset = {str(k) for k in actual_keys}

    def cell_value(cell):
        if isinstance(cell, dict):
            for k in ("value", "rawValue", "displayValue", "val", "amount"):
                if k in cell:
                    v = _clean_number(cell.get(k))
                    if v is not None:
                        return v
            # 값 객체가 한 단계 더 감싸진 경우
            for v in cell.values():
                if isinstance(v, (dict, list)):
                    got = cell_value(v)
                    if got is not None:
                        return got
        elif isinstance(cell, (str, int, float)):
            return _clean_number(cell)
        return None

    for src in sources:
        if isinstance(src, dict):
            for k, cell in src.items():
                val = cell_value(cell)
                if val is not None:
                    out[str(k)] = val
        elif isinstance(src, list):
            for idx, cell in enumerate(src):
                if isinstance(cell, dict):
                    key = cell.get("key") or cell.get("period") or cell.get("date") or cell.get("title")
                    val = cell_value(cell)
                    if key is not None and val is not None:
                        out[str(key)] = val
                    elif val is not None:
                        out[f"__idx_{idx}"] = val
                else:
                    val = cell_value(cell)
                    if val is not None:
                        out[f"__idx_{idx}"] = val
    # 기간 key가 문자열/숫자 타입 차이로 어긋난 경우에도 값이 있으면 반환
    return out


def _finance_row_tail_value(row: dict):
    """기간 키 매칭이 실패한 응답에서 해당 행의 마지막 수치값을 보조적으로 추출한다."""
    for key in ("columns", "values", "data", "cells", "valueList", "items"):
        src = row.get(key)
        vals=[]
        if isinstance(src, dict):
            iterable=list(src.values())
        elif isinstance(src, list):
            iterable=src
        else:
            continue
        for cell in iterable:
            if isinstance(cell, dict):
                for vk in ("value", "rawValue", "displayValue", "val", "amount"):
                    if vk in cell:
                        v=_clean_number(cell.get(vk))
                        if v is not None:
                            vals.append(v); break
            else:
                v=_clean_number(cell)
                if v is not None: vals.append(v)
        if vals:
            return vals[-1]
    return None
'''
s=s[:start]+new+s[end:]
# augment fetch_financials before explicit_debt line
needle='        explicit_debt=latest_row_value(["부채비율", "Debt Ratio", "DebtRatio"])\n'
replacement='''        # 네이버 통합정보의 totalInfos에 ROE가 노출되는 종목은 재무 API가 흔들려도 보조적으로 확보한다.\n        integration_roe = None\n        try:\n            integ = fetch_naver_integration(code)\n            integration_roe = _clean_number(integ.get("roe"))\n        except Exception:\n            pass\n\n        explicit_debt=latest_row_value(["부채비율", "Debt Ratio", "DebtRatio", "debtRatio"])\n'''
s=s.replace(needle,replacement)
# after explicit_roe line, apply integration roe
needle='        explicit_roe=latest_row_value(["ROE", "자기자본이익률", "자기자본 이익률"])\n'
replacement='''        explicit_roe=latest_row_value(["ROE", "자기자본이익률", "자기자본 이익률"])\n        if explicit_roe is None and integration_roe is not None:\n            explicit_roe=(integration_roe, latest_key, "totalInfos.roe")\n'''
s=s.replace(needle,replacement)
# fallback tail if latest_row_value no result
needle='''        net_income=latest_row_value(["당기순이익", "지배주주순이익", "당기순이익(지배)", "지배기업의소유주에게귀속되는당기순이익"])\n\n        debt_ratio = explicit_debt[0] if explicit_debt else None\n'''
replacement='''        net_income=latest_row_value(["당기순이익", "지배주주순이익", "당기순이익(지배)", "지배기업의소유주에게귀속되는당기순이익"])\n\n        # 실제 응답에서 기간 key가 바뀐 경우를 대비한 마지막 수치값 fallback\n        for aliases, holder in [\n            (["부채총계"], "debt"), (["자본총계", "자본총계(지배)", "지배기업소유주지분", "지배기업 소유주지분"], "equity"),\n            (["당기순이익", "지배주주순이익"], "net")]:\n            if locals()[holder] is None:\n                row=_find_finance_row(rows, aliases)\n                tail=_finance_row_tail_value(row) if row else None\n                if tail is not None:\n                    value=(tail, latest_key, str(row.get("title") or row.get("name") or row.get("label") or ""))\n                    if holder=="debt": debt=value\n                    elif holder=="equity": equity=value\n                    else: net_income=value\n\n        debt_ratio = explicit_debt[0] if explicit_debt else None\n'''
s=s.replace(needle,replacement)
# Modify integration return to include roe from totalInfos
needle='''        high = _clean_number(info_value({"highPriceOf52Weeks", "52주최고"}))\n        low = _clean_number(info_value({"lowPriceOf52Weeks", "52주최저"}))\n'''
replacement='''        high = _clean_number(info_value({"highPriceOf52Weeks", "52주최고"}))\n        low = _clean_number(info_value({"lowPriceOf52Weeks", "52주최저"}))\n        roe = _clean_number(info_value({"roe", "ROE", "returnOnEquity"}))\n'''
s=s.replace(needle,replacement)
needle='''            "investorTrendDate": trend_date or None,\n            "integrationSource": "m.stock.naver.com/integration",\n'''
replacement='''            "investorTrendDate": trend_date or None,\n            "roe": roe,\n            "integrationSource": "m.stock.naver.com/integration",\n'''
s=s.replace(needle,replacement)
# short batch auth precheck after import
needle='''    try:\n        from pykrx import stock as pykrx_stock\n    except Exception as e:\n'''
replacement='''    krx_id=os.getenv("KRX_ID")\n    krx_pw=os.getenv("KRX_PW")\n    if not krx_id or not krx_pw:\n        diagnostics["status"]="blocked"\n        diagnostics["message"]="KRX_ID/KRX_PW 미설정: 2026년 KRX 로그인 필요 정책으로 공매도 조회 불가"\n        for code in codes:\n            empty[code]["shortSellingStatus"]="blocked"\n            empty[code]["shortSellingMessage"]="KRX 로그인 인증정보 필요"\n        print("[공매도 차단] KRX_ID/KRX_PW 환경변수가 없어 KRX 조회를 건너뜁니다.")\n        return empty, diagnostics\n    try:\n        from pykrx import stock as pykrx_stock\n    except Exception as e:\n'''
s=s.replace(needle,replacement,1)
p.write_text(s,encoding='utf-8')
