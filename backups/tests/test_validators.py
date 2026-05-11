from django.test import TestCase
from django.core.exceptions import ValidationError
from backups.validators import validar_periodo, parsear_shortname, sanitizar_nombre_archivo


class ValidatorTests(TestCase):

    def test_periodo_valido(self):
        for p in ['20261', '20252', '20243']:
            validar_periodo(p)   # no debe lanzar

    def test_periodo_invalido(self):
        for p in ['2026', '202610', '20264', 'abc', '', '2026a']:
            with self.assertRaises(ValidationError):
                validar_periodo(p)

    def test_parsear_shortname_con_prefijo(self):
        r = parsear_shortname('CU-20261-11P240405-G03')
        self.assertEqual(r['anio'],     '2026')
        self.assertEqual(r['periodo'],  '20261')
        self.assertEqual(r['programa'], '11P240405')

    def test_parsear_shortname_sin_prefijo(self):
        r = parsear_shortname('20261-11P240405-G03')
        self.assertEqual(r['periodo'], '20261')

    def test_sanitizar_nombre(self):
        nombre = sanitizar_nombre_archivo('Historia/Peru:2026*?.mbz')
        self.assertNotIn('/', nombre)
        self.assertNotIn(':', nombre)
        self.assertNotIn('*', nombre)