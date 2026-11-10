import pandas as pd, re

# understands both output modes of the sketch (OUTPUT_CSV 1 or 0)
READABLE = re.compile(r'\[\s*([\d.]+) s\]\s+#\d+\s+(MOVING|still)')
def parse(line):
    if line.startswith('W,'):
        f = line.split(',')
        return int(f[2]), int(f[8] == '1')
    m = READABLE.match(line)
    if m:
        return int(float(m.group(1)) * 1000), int(m.group(2) == 'MOVING')
    return None

rows = [r for r in (parse(l.strip()) for l in open('log.txt')) if r]
df = pd.DataFrame(rows, columns=['ms', 'moving'])
df['t'] = df.ms / 1000

# group consecutive results with the same value into runs
df['run'] = (df.moving != df.moving.shift()).cumsum()
runs = df.groupby('run').agg(state=('moving', 'first'), start=('t', 'first'), n=('t', 'size')).reset_index(drop=True)
runs['duration_s'] = runs.n * 0.5          # 0.5 s per result at HOP_LEN = 25
runs['state'] = runs.state.map({1: 'MOVING', 0: 'still'})

# attach the most recent label you typed before each run started
lab = pd.read_csv('labels.csv')
lab['start'] = lab.ms / 1000
runs = pd.merge_asof(runs.sort_values('start'), lab[['start', 'label']].sort_values('start'),
                     on='start', direction='backward')
print(runs[['start', 'state', 'duration_s', 'label']].to_string(index=False))