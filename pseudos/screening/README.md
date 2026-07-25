# Screening pseudopotentials for nitride shortlists

Place UPF files here (or point `dft.pseudo_dir` at SSSP / your library).

Minimum for Nb–Ti–N shortlists:

| Element | Example |
|---------|---------|
| Nb | `Nb.pbe-spn-kjpaw_psl.0.3.0.UPF` (QE/SSSP PAW) |
| Ti | SSSP PBE efficiency or similar (prefer PAW for EPW) |
| N  | SSSP / QE `N.pbe-n-*.UPF` |

On Ubuntu with packages:

```bash
cp /usr/share/espresso/pseudo/Nb.pbe-spn-kjpaw_psl.0.3.0.UPF pseudos/screening/
cp /usr/share/espresso/pseudo/N.pbe-n-radius_5.UPF pseudos/screening/
# Ti often missing from distro trees — download from SSSP or use a matched set
```

Prefer a **consistent** PAW set (SSSP) over mixing PAW Nb with USPP Ti.
