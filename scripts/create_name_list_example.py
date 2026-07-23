#!/usr/bin/env python3
"""生成示例 name_list.xlsx（放在与两个仓库同级的工作目录）。"""

from __future__ import annotations

import argparse
from pathlib import Path

from openpyxl import Workbook


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("../name_list.xlsx"),
        help="输出路径，默认 ../name_list.xlsx",
    )
    args = parser.parse_args()
    out: Path = args.out
    out.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["nickname", "link"])
    ws.append(
        [
            "示例公众号请改成真实昵称",
            "https://mp.weixin.qq.com/s/请替换为真实文章短链",
        ]
    )
    wb.save(out)
    print(f"已写入: {out.resolve()}")


if __name__ == "__main__":
    main()
