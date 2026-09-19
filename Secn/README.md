# Wordlists collection

This repository contains a curated collection of wordlists commonly used for password auditing, security research, discovery, enumeration, fuzzing, and penetration testing.

Many of the most effective password lists are derived from real-world credential breaches (commonly known as _pwned passwords_), such as the well-known `rockyou.txt`. Others are generated from honeypot captures, malware campaigns, public leaks, dictionary sources, keyboard patterns, and targeted datasets.

> **Note:** Kali Linux ships with several password dictionaries by default. One of the most commonly used is available at:
>
> `/usr/share/wordlists/rockyou.txt.gz`

## Books

Wordlists derived from collections of book titles and literary works.

| Wordlist                                                                                                  |        Entries |
| --------------------------------------------------------------------------------------------------------- | -------------: |
| [Greatest books of all time (lowercase)](books/greatest_books_of_all_time_lowercase.txt)                  | _15,326 lines_ |
| [Greatest books of all time (original)](books/greatest_books_of_all_time_original.txt)                    |  _5,568 lines_ |
| [Greatest books of all time (uppercase)](books/greatest_books_of_all_time_uppercase.txt)                  | _15,326 lines_ |
| [Greatest books of all time (leet variations)](books/greatest_books_of_all_time_with_leet_variations.txt) | _31,700 lines_ |

## Common Passwords

Frequently used passwords collected from public studies, breach analyses, and security research.

| Wordlist                                                                               |            Entries |
| -------------------------------------------------------------------------------------- | -----------------: |
| [100k most used passwords (NCSC)](common-passwords/100k-most-used-passwords-NCSC.txt)  |     _99,840 lines_ |
| [10k most common passwords](common-passwords/10k-most-common.txt)                      |     _10,000 lines_ |
| [200 most used passwords (2020)](common-passwords/2020-200_most_used_passwords.txt)    |        _200 lines_ |
| [200 most used passwords (2023)](common-passwords/2023-200_most_used_passwords.txt)    |        _200 lines_ |
| [200 most used passwords (2024)](common-passwords/2024-200_most_used_passwords.txt)    |        _200 lines_ |
| [200 most used passwords (2025)](common-passwords/2025-200_most_used_passwords.txt)    |        _200 lines_ |
| [500 worst passwords](common-passwords/500-worst-passwords.txt)                        |        _500 lines_ |
| [Common passwords (Windows)](common-passwords/common-passwords-win.txt)                |        _815 lines_ |
| [Most popular letter-based passwords](common-passwords/most-popular-letter-passes.txt) |     _47,603 lines_ |
| [Probable v2 Top 12k](common-passwords/probable-v2_top-12000.txt)                      |     _12,645 lines_ |
| [PWDB Top 100k](common-passwords/Pwdb_top-100000.txt)                                  |    _100,000 lines_ |
| [PWDB Top 10 million](common-passwords/Pwdb_top-10000000.txt)                          | _10,000,000 lines_ |
| [Top SSH passwords](common-passwords/top-20-common-SSH-passwords.txt)                  |         _22 lines_ |
| [Xato 10 million passwords](common-passwords/xato-net-10-million-passwords.txt)        |  _5,189,454 lines_ |

## Cracked Hashes

Passwords recovered from publicly available cracked hash collections.

| Wordlist                                                    |        Entries |
| ----------------------------------------------------------- | -------------: |
| [Milw0rm dictionary](cracked-hashes/milw0rm-dictionary.txt) | _84,198 lines_ |

## Discovery

Wordlists intended for content discovery, web enumeration, DNS reconnaissance, and fuzzing.

| Wordlist                                                       |         Entries |
| -------------------------------------------------------------- | --------------: |
| [Backup files only](discovery/backup_files_only.txt)           |   _1,015 lines_ |
| [Backup files with path](discovery/backup_files_with_path.txt) |   _1,286 lines_ |
| [Common](discovery/common.txt)                                 |   _4,613 lines_ |
| [Directory only one](discovery/directory_only_one.txt)         |   _1,993 lines_ |
| [DNSMap domains](discovery/dnsmap.txt)                         |  _17,576 lines_ |
| [Sensitive files (Unix)](discovery/sensitive_files_unix.txt)   |      _16 lines_ |
| [Sensitive files (Windows)](discovery/sensitive_files_win.txt) |       _7 lines_ |
| [Subdomains](discovery/subdomains.txt)                         | _114,532 lines_ |

## Honeypot Captures

Passwords collected from internet-facing honeypots and attack telemetry.

| Wordlist                                                                                                 |         Entries |
| -------------------------------------------------------------------------------------------------------- | --------------: |
| [Multiple sources (Fabian Fingerle)](honeypot-captures/multiplesources-passwords-fabian-fingerle.de.txt) | _105,096 lines_ |
| [Python Heralding (September 2019)](honeypot-captures/python-heralding-sep2019.txt)                      |  _51,286 lines_ |

## Keyboard Walks

Passwords based on keyboard patterns and common typing habits.

| Wordlist                                                    |         Entries |
| ----------------------------------------------------------- | --------------: |
| [Keyboard combinations](keyboard-walks/keyboard-combos.txt) |   _9,608 lines_ |
| [Walk the line](keyboard-walks/walk-the-line.txt)           | _279,616 lines_ |

## Leaked Databases

Passwords extracted from publicly disclosed breaches and leaked credential datasets.

| Wordlist                                                                |            Entries |
| ----------------------------------------------------------------------- | -----------------: |
| [000webhost](leaked-databases/000webhost.txt)                           |    _720,302 lines_ |
| [Alleged Gmail passwords](leaked-databases/alleged-gmail-passwords.txt) |  _3,132,006 lines_ |
| [Ashley Madison](leaked-databases/Ashley-Madison.txt)                   |    _375,853 lines_ |
| [Dark Web 2017](leaked-databases/darkweb2017.txt)                       |      _9,999 lines_ |
| [Facebook phished](leaked-databases/facebook_phished.txt)               |      _2,441 lines_ |
| [Fortinet 2021 passwords](leaked-databases/fortinet-2021_passwords.txt) |     _78,977 lines_ |
| [Hak5](leaked-databases/hak5.txt)                                       |      _2,351 lines_ |
| [Hotmail](leaked-databases/hotmail.txt)                                 |      _8,930 lines_ |
| [Lizard Squad](leaked-databases/lizard-squad.txt)                       |     _11,781 lines_ |
| [MD5Decryptor UK](leaked-databases/md5decryptor-uk.txt)                 |  _3,431,316 lines_ |
| [Muslim Match](leaked-databases/muslimMatch.txt)                        |     _95,072 lines_ |
| [Nmap](leaked-databases/nmap.txt)                                       |      _4,999 lines_ |
| [NordVPN](leaked-databases/NordVPN.txt)                                 |      _1,437 lines_ |
| [phpBB cleaned-up](leaked-databases/phpbb-cleaned-up.txt)               |    _184,364 lines_ |
| [Porn (unknown source)](leaked-databases/porn-unknown.txt)              |      _8,088 lines_ |
| [RockYou](leaked-databases/rockyou.txt.bz2)                             | _14,344,392 lines_ |
| [Singles.org](leaked-databases/singles.org.txt)                         |     _12,233 lines_ |
| [Tuscl](leaked-databases/tuscl.txt)                                     |     _38,820 lines_ |
| [YouPorn 2012](leaked-databases/youporn2012.txt)                        |      _4,064 lines_ |

## Other

Miscellaneous and language-specific wordlists.

| Wordlist                                           |         Entries |
| -------------------------------------------------- | --------------: |
| [Italian wordlist (lowercase)](other/lower_it.txt) | _344,074 lines_ |
| [Italian wordlist (mixed)](other/mixed_it.txt)     |     _419 lines_ |

## Password Dictionaries

General-purpose dictionaries, dates, seasons, default credentials, and password permutations.

| Wordlist                                                                                            |           Entries |
| --------------------------------------------------------------------------------------------------- | ----------------: |
| [1337 Speak](password-dictionaries/1337speak.txt)                                                   |     _1,134 lines_ |
| [1900–2030 dates](password-dictionaries/1900-2030.txt)                                              |    _48,664 lines_ |
| [Conficker worm](password-dictionaries/conficker.txt)                                               |       _181 lines_ |
| [Darkc0de](password-dictionaries/darkc0de.txt)                                                      | _1,471,056 lines_ |
| [Days](password-dictionaries/days.txt)                                                              |     _6,240 lines_ |
| [Default passwords](password-dictionaries/default-passwords.txt)                                    |     _1,335 lines_ |
| [Default passwords for services](password-dictionaries/default_passwords_for_services.txt)          |     _1,243 lines_ |
| [KoreLogic password list](password-dictionaries/korelogic-password.txt)                             |     _1,152 lines_ |
| [Months](password-dictionaries/months.txt)                                                          |    _13,431 lines_ |
| [Password permutations](password-dictionaries/password-permutations.txt)                            |        _72 lines_ |
| [Merriam-Webster SCRABBLE (4th Edition)](password-dictionaries/Merriam-Webster-SCRABBLE-4thEdition) |    _45,949 lines_ |
| [SCRABBLE large](password-dictionaries/SCRABBLE-large)                                              |   _589,940 lines_ |
| [SCRABBLE munged large](password-dictionaries/SCRABBLE-munged-large)                                |   _597,337 lines_ |
| [Seasons](password-dictionaries/seasons.txt)                                                        |     _5,390 lines_ |

## WPA Passwords

Wordlists specifically tailored for WPA/WPA2 password auditing.

| Wordlist                                        |         Entries |
| ----------------------------------------------- | --------------: |
| [WPA over 200k](passwords-WPA/wpa-over200k.txt) | _203,806 lines_ |
| [WPA top 4.8k](passwords-WPA/wpa-top4800.txt)   |   _4,800 lines_ |
| [WPA top 447](passwords-WPA/wpa-top447.txt)     |     _447 lines_ |

## Software Generated

Wordlists distributed with or generated by popular password auditing tools.

| Wordlist                                               |           Entries |
| ------------------------------------------------------ | ----------------: |
| [BackTrack 4 passwords](software-gen/bt4-password.txt) | _1,652,903 lines_ |
| [Cain & Abel](software-gen/cain-and-abel.txt)          |   _306,706 lines_ |
| [John the Ripper](software-gen/john-the-ripper.txt)    |     _3,106 lines_ |

## Recommended Lists

For most use cases, the following wordlists provide the best balance between effectiveness and size:

### Password Auditing

- `rockyou.txt.bz2`
- `Pwdb_top-100000.txt`
- `100k-most-used-passwords-NCSC.txt`
- `xato-net-10-million-passwords.txt`
- `darkc0de.txt`

### Web Discovery & Enumeration

- `common.txt`
- `subdomains.txt`
- `dnsmap.txt`
- `backup_files_with_path.txt`

### WPA/WPA2 Auditing

- `wpa-over200k.txt`
- `wpa-top4800.txt`

### Default Credentials

- `default-passwords.txt`
- `default_passwords_for_services.txt`

## License

This project is licensed under the MIT Licence. See the [LICENSE](LICENSE) file for details.

## Disclaimer

This repository is intended for educational and authorised security testing purposes only. I don't promote unauthorised access, malicious activity, or misuse of the resources provided, and I will not be responsible for any illegal activities.
