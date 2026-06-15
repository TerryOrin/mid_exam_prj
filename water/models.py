from django.db import models


class Pond(models.Model):
    name = models.CharField(max_length=50, unique=True)
    description = models.CharField(max_length=200, blank=True)
    species = models.CharField(max_length=50, default="Fish")

    def __str__(self):
        return self.name


class SensorReading(models.Model):
    pond = models.ForeignKey(Pond, on_delete=models.CASCADE, related_name="readings")
    measured_at = models.DateTimeField(db_index=True)
    temperature = models.FloatField(help_text="Temperature in Celsius")
    ph = models.FloatField(help_text="pH value")
    dissolved_oxygen = models.FloatField(help_text="Dissolved oxygen in mg/L")
    ammonia = models.FloatField(help_text="Ammonia (NH3) in mg/L", null=True, blank=True)
    nitrite = models.FloatField(help_text="Nitrite (NO2-) in mg/L", null=True, blank=True)
    salinity = models.FloatField(help_text="Salinity in ppt", null=True, blank=True)

    class Meta:
        ordering = ["-measured_at"]
        indexes = [models.Index(fields=["pond", "-measured_at"])]

    def __str__(self):
        return f"{self.pond.name} @ {self.measured_at:%Y-%m-%d %H:%M}"
