"""Build self-hosted WOFF subsets from the official MaruBuri/Pretendard TTFs.

Usage: python scripts/build_diary_fonts.py --font-dir /path/to/original-fonts
The font directory must also contain Pretendard-LICENSE.txt (SIL OFL 1.1).
"""
from pathlib import Path
import argparse
from fontTools import subset
from fontTools.ttLib import TTFont

parser = argparse.ArgumentParser()
parser.add_argument('--font-dir', type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
output = root / 'static/fonts'
output.mkdir(exist_ok=True)
text = ''.join(path.read_text() for path in (root / 'templates').glob('*.html'))
text += (root / 'tasks/forms.py').read_text()
text += 'PERSONAL DAY LOG한 주의 할 일오늘의 할 일이날의 기록이날의 사진기억 속 문장유독 내 곁에오늘 하루는'
css = []
copyrights = []
for filename, family, weight in [('MaruBuri-SemiBold.ttf','DayLogSerif',600),
                                  ('Pretendard-Regular.ttf','DayLogSans',400),
                                  ('Pretendard-Bold.ttf','DayLogSans',700)]:
    font = TTFont(args.font_dir / filename)
    for record in font['name'].names:
        if record.nameID == 0 and record.platformID == 3 and record.toUnicode() not in copyrights:
            copyrights.append(record.toUnicode())
    options = subset.Options()
    options.name_IDs = ['*']; options.name_legacy = True; options.name_languages = ['*']
    tool = subset.Subsetter(options=options)
    characters = {ord(char) for char in text} | set(range(0x20,0x100))
    if family == 'DayLogSans':
        characters |= set(range(0xAC00,0xD7A4)) | set(range(0x1100,0x1200)) | set(range(0x3130,0x3190))
    tool.populate(unicodes=characters); tool.subset(font)
    primary_name = 'DayLog Serif Subset' if family == 'DayLogSerif' else 'DayLog Sans Subset'
    style = {400:'Regular',600:'SemiBold',700:'Bold'}[weight]
    for record in font['name'].names:
        if record.nameID in (1,3,4,6,16,17):
            value={1:primary_name,3:f'{primary_name} {style}',4:f'{primary_name} {style}',
                   6:f'{primary_name.replace(" ","")}-{style}',16:primary_name,17:style}[record.nameID]
            record.string=value.encode(record.getEncoding(),errors='replace')
    font.flavor='woff'
    destination=output / f'{family.lower()}-{weight}.woff'
    font.save(destination)
    css.append(f"@font-face{{font-family:{family};font-style:normal;font-weight:{weight};font-display:swap;src:url('{destination.name}') format('woff')}}")
    print(f'{destination.name}: {destination.stat().st_size:,} bytes')
(output / 'daylog-fonts.css').write_text('\n'.join(css)+'\n')
license_text=(args.font_dir / 'Pretendard-LICENSE.txt').read_text()
ofl=license_text[license_text.index('SIL OPEN FONT LICENSE'):]
notice='''Font sources: https://hangeul.naver.com/font and https://github.com/orioncactus/pretendard
The embedded files are subsets named DayLog Serif Subset and DayLog Sans Subset.
Original copyright and SIL OFL notices follow. The application source has its own license.
\n'''
notice+='\n'.join(copyrights)+'\n\n'+license_text+'\n\n'
notice+='Copyright (c) 2010, NAVER Corporation (https://www.navercorp.com/) with Reserved Font Names Nanum, Naver Nanum, NanumGothic, Naver NanumGothic, NanumMyeongjo, Naver NanumMyeongjo, NanumBrush, Naver NanumBrush, NanumPen, Naver NanumPen, Naver NanumGothicEco, NanumGothicEco, Naver NanumMyeongjoEco, NanumMyeongjoEco, Naver NanumGothicLight, NanumGothicLight, NanumBarunGothic, Naver NanumBarunGothic, NanumSquareRound, NanumBarunPen, MaruBuri, NanumSquareNeo.\nThis Font Software is licensed under the SIL Open Font License, Version 1.1.\n\n'+ofl
(output / 'LICENSE.txt').write_text('\n'.join(line.rstrip() for line in notice.splitlines())+'\n')
