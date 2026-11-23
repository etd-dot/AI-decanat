#pragma once

#include <cstdint>

// Independent of Unreal so cancellation and replacement can be checked without the editor.
class FDecanatMotion
{
public:
    enum class EPose { Seated, Rising, Standing, Sitting };

    std::uint64_t Begin(bool bStand)
    {
        Pose = bStand ? EPose::Rising : EPose::Sitting;
        return ++Revision;
    }

    bool Complete(std::uint64_t Ticket)
    {
        if (Ticket != Revision || !IsMoving()) return false;
        Pose = Pose == EPose::Rising ? EPose::Standing : EPose::Seated;
        return true;
    }

    void Reset()
    {
        ++Revision;
        Pose = EPose::Seated;
    }

    bool IsMoving() const { return Pose == EPose::Rising || Pose == EPose::Sitting; }
    bool IsStanding() const { return Pose == EPose::Standing; }
    EPose GetPose() const { return Pose; }

private:
    std::uint64_t Revision = 0;
    EPose Pose = EPose::Seated;
};
