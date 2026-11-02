// Copyright (c) Kai Ase Siren. Licensed under the MIT License.

namespace EcoTelemetry.Tests.Throwing;

// A component whose getters throw, the dangling-reference case.
internal sealed class AirPollutionComponent
{
    public float PollutionTonsPerHour => throw new InvalidOperationException("dangling");

    public float TotalTonsPollution => throw new InvalidOperationException("dangling");
}
